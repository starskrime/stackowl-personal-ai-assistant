"""Unit coverage for the side-effect-aware give-up seam (L1), L3 refusal, L2 prompt.

- L1: ``is_effectful_failure`` truth table — the single predicate shared by the
  ledger tally, the execute snapshot, and the give-up floor.
- L1: ``ToolResult.side_effect_committed`` defaults True (backward-compatible) and a
  pre-execution refusal can mark it False.
- L2: the router prompt now invites direct-answer (teaching/explain) requests into
  the ``conversational`` class, not only social chit-chat.
"""

from __future__ import annotations

from stackowl.infra.tool_outcome_ledger import (
    ToolOutcome,
    consequential_tally,
    is_effectful_failure,
)
from stackowl.tools.base import ToolResult


def test_is_effectful_failure_truth_table() -> None:
    # A failed write that crossed the boundary IS an unachieved effect → floor.
    assert is_effectful_failure("write", success=False, side_effect_committed=True) is True
    assert is_effectful_failure("consequential", success=False, side_effect_committed=True) is True
    # A validation-refused / no-op write did nothing → NOT an unachieved effect.
    assert is_effectful_failure("write", success=False, side_effect_committed=False) is False
    assert is_effectful_failure("consequential", success=False, side_effect_committed=False) is False
    # A successful effect is never a give-up.
    assert is_effectful_failure("write", success=True, side_effect_committed=True) is False
    # Read-severity failures never count.
    assert is_effectful_failure("read", success=False, side_effect_committed=True) is False
    # Default side_effect_committed is conservative (True) — undeclared failures floor.
    assert is_effectful_failure("write", success=False) is True


def test_tool_result_side_effect_committed_default_and_override() -> None:
    assert ToolResult(success=False, output="", duration_ms=1.0).side_effect_committed is True
    refused = ToolResult(success=False, output="", duration_ms=1.0, side_effect_committed=False)
    assert refused.side_effect_committed is False


def test_consequential_tally_excludes_refused_no_op(monkeypatch) -> None:
    from stackowl.infra import tool_outcome_ledger as ledger

    token = ledger.bind()
    try:
        # A refused write (no side effect) and a real failed write.
        ledger._outcomes.set((
            ToolOutcome("memory", "write", success=False, side_effect_committed=False),
            ToolOutcome("save_note", "write", success=False, side_effect_committed=True),
        ))
        cons_f, cons_s = consequential_tally()
        assert cons_f == 1, "only the genuine (committed) write failure counts"
        assert cons_s == 0
    finally:
        ledger.reset(token)


def test_router_prompt_invites_direct_answer_into_conversational() -> None:
    from stackowl.owls.registry import OwlRegistry
    from stackowl.owls.router import SecretaryRouter
    from stackowl.providers.registry import ProviderRegistry

    router = SecretaryRouter(ProviderRegistry(), OwlRegistry.with_default_secretary())
    prompt = router._build_prompt([("secretary", "general assistant")], "explain recursion")
    low = prompt.lower()
    # The conversational class is no longer social-only: a request answerable directly
    # from the model's own knowledge (definition / how-to / explanation / advice /
    # mnemonic) belongs there. Assert the broadened guidance is present.
    assert "conversational" in low
    assert "external action" in low
    assert any(k in low for k in ("explain", "definition", "mnemonic", "advice")), (
        f"router prompt did not broaden conversational to direct-answer requests: {prompt!r}"
    )
    # The fail-safe to 'standard' for action-requiring requests is preserved.
    assert "standard" in low
