"""Anti-mock lint — gateway command tests must drive registry.dispatch, not .handle().

This guard prevents regression to mock-only tests in tests/journeys/commands/.
Any new test file in this directory must:
  - Drive commands via CommandRegistry.dispatch (or register_all_commands),
    NOT by calling command.handle() directly.
  - NOT construct EventBus() directly (that leaks implementation detail;
    use fakes or MagicMock instead).

Implementation: read the .py files in this directory and assert the banned
substrings are absent, excluding this meta-test itself.
"""

from __future__ import annotations

from pathlib import Path

_THIS_FILE = Path(__file__).name
_BANNED_SUBSTRINGS = (
    ".handle(",   # direct handle() call bypasses registry dispatch
    "EventBus(",  # direct EventBus construction in gateway tests
)


def test_no_direct_handle_or_eventbus_in_gateway_command_tests() -> None:
    """No file in this directory (besides this one) may call .handle( or EventBus(."""
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
