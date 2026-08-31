"""Was the criterion met? — judged, then VETOED by what the turn actually did.

Second half of the completion contract (Bakir, 2026-08-31). The first half
writes what would count as done, from the request, before the work
(TurnAchievementWriter). This half asks whether it happened.

TWO THINGS COME BACK FROM ONE CALL, deliberately. The judge returns both its
verdict AND whether the criterion requires an ARTIFACT — a file the user
receives — because the structural veto needs to know that and re-deriving it
would cost a third call. Bakir authorised two calls per turn; this keeps it to
two.

**STRUCTURE OVERRULES THE JUDGE, ONE WAY ONLY.** His rule verbatim: "If the
criterion names an artifact and no artifact exists, the turn is unachieved no
matter what the judge says. The judge can only downgrade, never upgrade." That
asymmetry is the whole point — a confident judge marking "I'll draw this as an
actual image for you" as ACHIEVED is exactly how the 2026-08-31 incident would
repeat, and this programme has already measured LLM judges near chance when
assessing a trajectory they can see.

WHY STRUCTURE CANNOT STAND ALONE. "Explain me BFS" legitimately runs no tools
and produces no artifact, and it is genuinely achieved. So "ran nothing" is not
evidence of failure; it only becomes evidence once the criterion says an
artifact was owed. Structure vetoes, it does not detect.

SHADOW: the verdict is logged. Nothing is reopened yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.turn_achievement_judge import (
    AchievementVerdict,
    TurnAchievementJudge,
)
from stackowl.interaction.turn_achievement_writer import DEFAULT_ACHIEVEMENT
from stackowl.providers.base import CompletionResult, Message, ModelProvider


class _FakeProvider(ModelProvider):
    def __init__(
        self, canned: str, *, raise_on_complete: Exception | None = None,
        hang_seconds: float | None = None,
    ) -> None:
        self._canned = canned
        self._raise = raise_on_complete
        self._hang = hang_seconds
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "fake-fast"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        self.calls.append(list(messages))
        if self._hang is not None:
            await asyncio.sleep(self._hang)
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._canned, input_tokens=1, output_tokens=1,
            model="m", provider_name=self.name, duration_ms=1.0,
        )

    async def stream(  # pragma: no cover — unused
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield ""


class _FakeRegistry:
    def __init__(self, provider: ModelProvider | None, *, raise_on_get: Exception | None = None):
        self._p = provider
        self._raise = raise_on_get

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        if self._raise is not None:
            raise self._raise
        assert self._p is not None
        return self._p, "fake-model"


def _judge(canned: str = "ACHIEVED ARTIFACT", **kw: object) -> TurnAchievementJudge:
    provider = None if kw.get("raise_on_get") else _FakeProvider(
        canned,
        raise_on_complete=kw.get("raise_on_complete"),  # type: ignore[arg-type]
        hang_seconds=kw.get("hang_seconds"),  # type: ignore[arg-type]
    )
    reg = _FakeRegistry(provider, raise_on_get=kw.get("raise_on_get"))  # type: ignore[arg-type]
    return TurnAchievementJudge(reg, timeout_s=float(kw.get("timeout_s", 3.0)))  # type: ignore[arg-type]


# =========================================================================== #
# 1. The veto — the reason this exists
# =========================================================================== #


@pytest.mark.asyncio
async def test_the_incident_a_promised_artifact_that_never_landed_is_NOT_achieved() -> None:
    """The judge is fooled; structure is not. Verbatim from 2026-08-31."""
    judge = _judge("ACHIEVED ARTIFACT")  # the judge believes the promise
    verdict = await judge.judge(
        criterion="a picture of the BFS tree is delivered to the user",
        result="I'll draw this as an actual image for you.",
        produced_artifact=False,
    )
    assert verdict.achieved is False
    assert verdict.vetoed_by_structure is True


@pytest.mark.asyncio
async def test_the_same_turn_WITH_an_artifact_is_achieved() -> None:
    judge = _judge("ACHIEVED ARTIFACT")
    verdict = await judge.judge(
        criterion="a picture of the BFS tree is delivered to the user",
        result="Here is the diagram.",
        produced_artifact=True,
    )
    assert verdict.achieved is True
    assert verdict.vetoed_by_structure is False


@pytest.mark.asyncio
async def test_structure_can_only_DOWNGRADE_never_upgrade() -> None:
    """An artifact existing does not rescue a NOT verdict.

    The asymmetry is the safety property: if structure could upgrade, a turn
    that wrote any file would pass regardless of what was asked.
    """
    judge = _judge("NOT ARTIFACT")
    verdict = await judge.judge(
        criterion="a picture of the BFS tree is delivered to the user",
        result="Here is an unrelated file.",
        produced_artifact=True,
    )
    assert verdict.achieved is False


# =========================================================================== #
# 2. A text answer is a real answer — structure must not punish it
# =========================================================================== #


@pytest.mark.asyncio
async def test_a_conversational_answer_needs_no_artifact() -> None:
    """"Explain me BFS" runs no tools and is genuinely achieved.

    If this ever fails, the veto has become a detector and will floor every
    ordinary answer on the platform.
    """
    judge = _judge("ACHIEVED TEXT")
    verdict = await judge.judge(
        criterion="an explanation of BFS for trees, with a Python code example",
        result="BFS explores level by level... here is the code...",
        produced_artifact=False,
    )
    assert verdict.achieved is True
    assert verdict.vetoed_by_structure is False


# =========================================================================== #
# 3. No opinion where none is possible
# =========================================================================== #


@pytest.mark.asyncio
async def test_the_default_constant_is_not_judged_at_all() -> None:
    """It is true of every turn, so judging it would manufacture a verdict.

    Also saves the call: no criterion, no question worth asking.
    """
    provider = _FakeProvider("ACHIEVED TEXT")
    judge = TurnAchievementJudge(_FakeRegistry(provider), timeout_s=1.0)  # type: ignore[arg-type]
    verdict = await judge.judge(
        criterion=DEFAULT_ACHIEVEMENT, result="anything", produced_artifact=False
    )
    assert verdict.achieved is None
    assert provider.calls == [], "the provider was called for a criterion worth nothing"


@pytest.mark.parametrize(
    "kw",
    [
        {"raise_on_get": RuntimeError("no provider")},
        {"raise_on_complete": RuntimeError("boom")},
        {"canned": ""},
        {"canned": "maybe possibly"},
    ],
)
@pytest.mark.asyncio
async def test_every_degraded_path_is_NO_OPINION_never_a_failure(kw: dict) -> None:
    """Fail-safe is silence, not accusation.

    A wrong "not achieved" would reopen a turn that was fine — in shadow that is
    a bad measurement, and under enforcement it would repeat answers at the user.
    """
    verdict = await _judge(**kw).judge(
        criterion="a picture is delivered to the user", result="here", produced_artifact=True
    )
    assert verdict.achieved is None


@pytest.mark.asyncio
async def test_a_hanging_judge_does_not_hold_anything() -> None:
    verdict = await _judge(hang_seconds=5.0, timeout_s=0.2).judge(
        criterion="a picture is delivered to the user", result="here", produced_artifact=True
    )
    assert verdict.achieved is None


@pytest.mark.asyncio
async def test_an_empty_result_is_never_achieved() -> None:
    """Nothing reached the user, so nothing was achieved — no call needed."""
    provider = _FakeProvider("ACHIEVED TEXT")
    judge = TurnAchievementJudge(_FakeRegistry(provider), timeout_s=1.0)  # type: ignore[arg-type]
    verdict = await judge.judge(
        criterion="a picture is delivered to the user", result="  ", produced_artifact=False
    )
    assert verdict.achieved is False
    assert provider.calls == []


def test_the_verdict_carries_why() -> None:
    """A shadow verdict nobody can explain is not evidence."""
    v = AchievementVerdict(achieved=False, requires_artifact=True, vetoed_by_structure=True)
    assert v.reason
