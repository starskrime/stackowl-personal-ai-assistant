"""E2-S4 — make_budget_callback: breach → raise; interactive → clarify raise/stop."""

from __future__ import annotations

import pytest

from stackowl.exceptions import BudgetBreach
from stackowl.pipeline.budget.callback import make_budget_callback
from stackowl.providers.react_callback import ReActIterationState


class _GovStub:
    def __init__(self, breach: BudgetBreach | None) -> None:
        self._breach = breach
        self.raised: list[str] = []

    def check(self, iteration: int) -> BudgetBreach | None:
        return self._breach

    def raise_caps(self, cap: str) -> None:
        self.raised.append(cap)
        self._breach = None


class _Clarify:
    def __init__(self, answer: str | None) -> None:
        self._answer = answer

    async def ask(self, session_id, channel, question, *, choices=(), blocking=False):  # noqa: ANN001
        return "cid"

    async def wait_for_answer(self, clarify_id, timeout):  # noqa: ANN001
        return (self._answer, None)


_ITER = ReActIterationState(iteration=1, messages=[{"role": "assistant", "content": "partial"}],
                            tool_call_records=[{"name": "x"}])


async def test_no_breach_is_passthrough() -> None:
    cb = make_budget_callback(_GovStub(None), interactive=False, clarify=None,
                              session_id="s", channel="cli")
    await cb(_ITER)


async def test_non_interactive_breach_raises() -> None:
    breach = BudgetBreach("steps", 2, 2)
    cb = make_budget_callback(_GovStub(breach), interactive=False, clarify=None,
                              session_id="s", channel="cli")
    with pytest.raises(BudgetBreach) as ei:
        await cb(_ITER)
    assert ei.value.cap == "steps"
    assert ei.value.partial_text == "partial"
    assert ei.value.tool_call_records == [{"name": "x"}]


async def test_interactive_raise_continues() -> None:
    gov = _GovStub(BudgetBreach("steps", 2, 2))
    cb = make_budget_callback(gov, interactive=True, clarify=_Clarify("Raise"),
                              session_id="s", channel="cli")
    await cb(_ITER)
    assert gov.raised == ["steps"]


async def test_interactive_stop_raises() -> None:
    cb = make_budget_callback(_GovStub(BudgetBreach("steps", 2, 2)), interactive=True,
                              clarify=_Clarify("Stop"), session_id="s", channel="cli")
    with pytest.raises(BudgetBreach):
        await cb(_ITER)


async def test_interactive_timeout_fails_closed() -> None:
    cb = make_budget_callback(_GovStub(BudgetBreach("cost", 1, 2)), interactive=True,
                              clarify=_Clarify(None), session_id="s", channel="cli")
    with pytest.raises(BudgetBreach):
        await cb(_ITER)
