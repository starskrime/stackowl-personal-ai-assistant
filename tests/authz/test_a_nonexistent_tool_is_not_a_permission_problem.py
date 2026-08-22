"""A tool that does not exist must not be reported as a bounds refusal.

MEASURED LIVE 2026-08-22 02:45:04: `[pipeline] execute: tool refused by bounds
tool=memory_set owl=mailbutler`. There is no `memory_set` tool — the real one is
`memory`. The model hallucinated a name, and the platform told it that it lacked
PERMISSION for something that cannot exist.

WHY THIS IS NOT COSMETIC. `blocked_capability_of` reads a bounds refusal as a
capability block, so the loop requeues the turn with its standard remedy: "ask the
user to grant it — owl_build action='grant' with explicit_tools=['memory_set']".
The operator gets asked to grant a capability that can never exist, the retry
ceiling burns on an impossible goal, and the model never learns the name was
simply wrong. An unwinnable healing loop.

It also SHADOWED the correct recovery. The unknown-tool branch returns "Tool 'X'
does not exist ... build it with tool_build (or author a skill)" — the
self-extension path this platform is built around — and for any hallucinated name
outside the owl's bounds it was unreachable, because bounds was checked first.

The ordering now matches the capability check five lines above it, which carries
the same reasoning in its own comment: "this cannot run" is a cheaper and more
honest answer than "you may not run this".
"""

from __future__ import annotations

import inspect

from stackowl.pipeline.steps import execute as execute_mod

_SRC = inspect.getsource(execute_mod)


def test_existence_is_checked_BEFORE_bounds() -> None:
    """The ordering IS the fix, so the ordering is what gets asserted.

    Driving a full dispatch here would need a provider, a registry, a ledger and a
    progress tracker; the defect is a single ordering fact and this states it
    directly. A mutation that swaps the two checks back fails this immediately.
    """
    exists_check = _SRC.index("if _tool_obj is None:")
    bounds_check = _SRC.index("bounds_block = check_effective_bounds(")
    assert exists_check < bounds_check, (
        "the bounds check runs before the existence check — a hallucinated tool "
        "name will be reported as a permission problem, and the loop will ask the "
        "operator to grant a capability that cannot exist"
    )


def test_the_nonexistent_message_points_at_SELF_EXTENSION_not_at_permission() -> None:
    """What the agent is told decides what it does next. 'You may not' sends it to
    owl_build; 'does not exist' sends it to tool_build, which is the only one of
    the two that can ever succeed here."""
    start = _SRC.index("if _tool_obj is None:")
    block = _SRC[start:start + 1200]
    assert "does not exist" in block
    assert "tool_build" in block
    assert "bounds" not in block.split("return")[1][:400], (
        "the not-exists refusal must not mention bounds — that is the confusion "
        "this test exists to prevent"
    )


def test_the_unknown_tool_failure_is_LEDGERED_not_silent() -> None:
    """RC1 (2026-07-08): an unknown-tool return that bypasses the ledger is
    indistinguishable from a successful call to the circuit breaker and the
    delivery judge. Moving the check earlier must not lose that."""
    start = _SRC.index("if _tool_obj is None:")
    block = _SRC[start:start + 1200]
    assert "record_tool_outcome" in block, "the failure must reach the ledger"
    assert "record_no_progress" in block, "the circuit breaker must count it"
    assert "TOOL_FAILED_MARKER" in block, "the judge must see a real failure"
