"""Anti-mock lint — gateway command tests must drive registry.dispatch, not .handle().

This guard prevents regression to mock-only tests in tests/journeys/commands/.
Any new test file in this directory must drive commands via
CommandRegistry.dispatch (or register_all_commands), NOT by calling
command.handle() directly. A direct .handle() call bypasses the registry +
assembler — the exact way the old provider test masked the dead-bus bug.

NOTE on EventBus: passing a REAL ``EventBus()`` through
``CommandDeps(event_bus=...)`` and then dispatching is the CORRECT
production-path test (it proves the assembler-wired command emits on the real
bus). So EventBus construction is NOT banned here — only ``.handle(`` is. The
"tell" of a mock-only test is the dispatch bypass, not the bus.

Implementation: read the .py files in this directory and assert the banned
substring is absent, excluding this meta-test itself.
"""

from __future__ import annotations

from pathlib import Path

_THIS_FILE = Path(__file__).name
_BANNED_SUBSTRINGS = (
    ".handle(",   # direct handle() call bypasses registry dispatch + assembler
)


def test_no_direct_handle_or_eventbus_in_gateway_command_tests() -> None:
    """No file in this directory (besides this one) may call .handle( directly."""
    this_dir = Path(__file__).parent
    py_files = [
        f for f in this_dir.glob("*.py")
        if f.name != _THIS_FILE and not f.name.startswith("__")
    ]

    violations: list[str] = []
    for py_file in sorted(py_files):
        src = py_file.read_text(encoding="utf-8")
        for banned in _BANNED_SUBSTRINGS:
            if banned in src:
                violations.append(f"{py_file.name}: contains {banned!r}")

    assert not violations, (
        "Gateway command tests must drive CommandRegistry.dispatch, not .handle().\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
