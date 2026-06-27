"""F-15 — the delivery judge no longer rubber-stamps give-ups on judge-error.

``judge_delivery`` historically failed OPEN on ANY provider error / unparseable
verdict: ``(True, JUDGE_ERROR_REASON)``. That silently accepts a give-up whenever
the judge model is flaky. The fix:

  * a ``fallback_provider`` is retried ONCE when the primary judge cannot vet;
  * for a ``consequential`` turn, an unvettable judge fails toward "not delivered /
    continue" (a genuine give-up verdict the caller will act on), NOT accept;
  * a non-consequential turn preserves the historical fail-OPEN so ordinary chat is
    never blocked by a flaky judge.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.persistence import (
    JUDGE_CONSEQUENTIAL_FAILSAFE,
    JUDGE_ERROR_REASON,
    judge_delivery,
)

pytestmark = pytest.mark.asyncio


class _Completion:
    def __init__(self, content: str) -> None:
        self.content = content


class _RaisingJudge:
    async def complete(self, *a: object, **k: object) -> _Completion:
        raise RuntimeError("judge down")


class _DeliveredJudge:
    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": true, "reason": "ok"}')


class _GaveUpJudge:
    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion('{"delivered": false, "reason": "stub"}')


class _EmptyJudge:
    async def complete(self, *a: object, **k: object) -> _Completion:
        return _Completion("")


async def test_fallback_provider_retried_on_primary_error() -> None:
    delivered, reason = await judge_delivery(
        _RaisingJudge(), "req", "draft", [],
        fallback_provider=_DeliveredJudge(),
    )
    assert delivered is True
    assert reason == "ok"


async def test_fallback_can_rule_giveup() -> None:
    delivered, reason = await judge_delivery(
        _EmptyJudge(), "req", "draft", [],
        fallback_provider=_GaveUpJudge(),
    )
    assert delivered is False
    assert reason == "stub"


async def test_consequential_failsafe_when_both_unvettable() -> None:
    delivered, reason = await judge_delivery(
        _RaisingJudge(), "req", "draft", [],
        fallback_provider=_EmptyJudge(),
        consequential=True,
    )
    assert delivered is False
    # A distinct, vettable reason (NOT the fail-open sentinel) so the caller treats
    # it as a genuine give-up and CONTINUES rather than shipping an unvetted draft.
    assert reason == JUDGE_CONSEQUENTIAL_FAILSAFE
    assert reason != JUDGE_ERROR_REASON


async def test_consequential_no_fallback_still_failsafe() -> None:
    delivered, reason = await judge_delivery(
        _RaisingJudge(), "req", "draft", [], consequential=True,
    )
    assert delivered is False
    assert reason == JUDGE_CONSEQUENTIAL_FAILSAFE


async def test_non_consequential_preserves_fail_open() -> None:
    delivered, reason = await judge_delivery(
        _RaisingJudge(), "req", "draft", [],
    )
    assert delivered is True
    assert reason == JUDGE_ERROR_REASON


async def test_non_consequential_fail_open_even_with_failing_fallback() -> None:
    delivered, reason = await judge_delivery(
        _RaisingJudge(), "req", "draft", [],
        fallback_provider=_EmptyJudge(),
    )
    assert delivered is True
    assert reason == JUDGE_ERROR_REASON


async def test_primary_success_no_fallback_call() -> None:
    """A clean primary verdict is returned verbatim; the fallback is untouched."""
    calls: list[int] = []

    class _CountingFallback:
        async def complete(self, *a: object, **k: object) -> _Completion:
            calls.append(1)
            return _Completion('{"delivered": false, "reason": "x"}')

    delivered, reason = await judge_delivery(
        _DeliveredJudge(), "req", "draft", [],
        fallback_provider=_CountingFallback(), consequential=True,
    )
    assert delivered is True
    assert reason == "ok"
    assert calls == []
