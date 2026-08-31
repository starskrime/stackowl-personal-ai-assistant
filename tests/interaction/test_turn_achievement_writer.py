"""A task's achievement condition must describe THE GOAL, not delivery.

EARNED 2026-08-31, live. Bakir asked "Give me in pictures". The turn ran with
``tools_used=false``, the model replied "I'll draw this as an actual image for
you", no image tool was ever called, and the task closed as ``completed``. His
question was the right one: *why is the task marked completed?*

Because ``turn_task.py`` hardcodes
``achievement="the reply is delivered to the user who asked"`` for every chat
turn. All three distinct achievement values across 1,283 live tasks are
restatements of delivery, so completion checks delivery twice and the goal zero
times. A promise is delivered, therefore the goal is achieved.

This writes a REAL criterion, from the REQUEST ONLY, BEFORE the work runs, so it
cannot be retrofitted to whatever happened. Shadow phase: it is stored and
logged; nothing judges it and nothing reopens yet.

THE DEGENERACY GUARD IS STRUCTURAL AND LANGUAGE-INDEPENDENT. "Force it to name
the observable" cannot be a list of English words — the platform is multilingual
([[feedback_no_hardcoded_english]], [[feedback_no_hardcoded_keyword_lists]]). A
criterion that shares NO content token with the request is generic by
construction: "a reply is delivered to the user" has nothing in common with
"Give me in pictures". That test works in any script.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

import pytest

from stackowl.interaction.turn_achievement_writer import (
    DEFAULT_ACHIEVEMENT,
    TurnAchievementWriter,
)
from stackowl.providers.base import CompletionResult, Message, ModelProvider


class _FakeProvider(ModelProvider):
    def __init__(
        self,
        canned: str,
        *,
        raise_on_complete: Exception | None = None,
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
            model="fake-model", provider_name=self.name, duration_ms=1.0,
        )

    async def stream(  # pragma: no cover — unused
        self, messages: list[Message], model: str, **kwargs: object
    ) -> AsyncIterator[str]:
        yield ""


class _FakeRegistry:
    def __init__(
        self, provider: ModelProvider | None = None, *, raise_on_get: Exception | None = None
    ) -> None:
        self._provider = provider
        self._raise = raise_on_get

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        if self._raise is not None:
            raise self._raise
        assert self._provider is not None
        return self._provider, "fake-fast-model"


def _make(
    canned: str = "an image file is delivered to the user",
    *,
    raise_on_complete: Exception | None = None,
    hang_seconds: float | None = None,
    raise_on_get: Exception | None = None,
    timeout_s: float = 3.0,
) -> TurnAchievementWriter:
    provider = None if raise_on_get else _FakeProvider(
        canned, raise_on_complete=raise_on_complete, hang_seconds=hang_seconds
    )
    registry = _FakeRegistry(provider, raise_on_get=raise_on_get)
    return TurnAchievementWriter(registry, timeout_s=timeout_s)  # type: ignore[arg-type]


# =========================================================================== #
# 1. The live case
# =========================================================================== #


@pytest.mark.asyncio
async def test_the_incident_request_gets_a_criterion_naming_the_artifact() -> None:
    writer = _make("a picture of the BFS tree is delivered to the user")
    got = await writer.write(request="Give me in pictures")
    assert got == "a picture of the BFS tree is delivered to the user"
    assert got != DEFAULT_ACHIEVEMENT


@pytest.mark.asyncio
async def test_inflection_alone_does_not_refuse_a_good_criterion() -> None:
    """picture/pictures. The FIRST version of this guard refused exactly this.

    It also refused "an image file..." against "Give me in pictures" — synonyms
    are not shared tokens — which is why the prompt now requires reusing the
    user's own words. Recorded because the guard looked obviously right and was
    not.
    """
    writer = _make("a picture is delivered to the user")
    assert await writer.write(request="Give me in pictures") != DEFAULT_ACHIEVEMENT


@pytest.mark.asyncio
async def test_a_conversational_request_still_gets_a_specific_criterion() -> None:
    """Most turns have no artifact. That is not a licence to be generic."""
    writer = _make("an explanation of BFS for trees, with a Python code example")
    got = await writer.write(request="Explain me in easy way how to remember bfs for tree in python")
    assert "BFS" in got


# =========================================================================== #
# 2. The degeneracy guard — the whole point of the item
# =========================================================================== #


@pytest.mark.parametrize(
    "degenerate",
    [
        "the reply is delivered to the user who asked",
        "a response is sent",
        "the user receives an answer",
    ],
)
@pytest.mark.asyncio
async def test_a_criterion_that_restates_delivery_is_refused(degenerate: str) -> None:
    """True of every turn ever, therefore worthless as a criterion.

    Caught structurally: it shares no content token with the request.
    """
    writer = _make(degenerate)
    got = await writer.write(request="Give me in pictures")
    assert got == DEFAULT_ACHIEVEMENT, "a delivery-restatement was accepted as a criterion"


@pytest.mark.asyncio
async def test_the_guard_is_not_english_specific() -> None:
    """Overlap works in any script — the platform is multilingual.

    Azerbaijani request, Azerbaijani criterion that genuinely references it.
    """
    writer = _make("istifadəçiyə şəkil faylı çatdırılır")
    got = await writer.write(request="Mənə şəkil ver")
    assert got == "istifadəçiyə şəkil faylı çatdırılır"


@pytest.mark.asyncio
async def test_a_criterion_sharing_nothing_with_the_request_is_refused() -> None:
    writer = _make("the quarterly revenue spreadsheet is exported")
    got = await writer.write(request="Give me in pictures")
    assert got == DEFAULT_ACHIEVEMENT


# =========================================================================== #
# 3. Fail-safe: never worse than today
# =========================================================================== #


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raise_on_get": RuntimeError("no provider")},
        {"raise_on_complete": RuntimeError("provider exploded")},
        {"canned": ""},
        {"canned": "   \n  "},
    ],
)
@pytest.mark.asyncio
async def test_every_degraded_path_falls_back_to_todays_constant(kwargs: dict) -> None:
    """Fail-safe direction is the CURRENT behaviour, so this can never regress a turn."""
    writer = _make(**kwargs)
    assert await writer.write(request="Give me in pictures") == DEFAULT_ACHIEVEMENT


@pytest.mark.asyncio
async def test_a_hanging_provider_does_not_hold_the_turn() -> None:
    writer = _make(hang_seconds=5.0, timeout_s=0.2)
    assert await writer.write(request="Give me in pictures") == DEFAULT_ACHIEVEMENT


@pytest.mark.asyncio
async def test_an_empty_request_does_not_call_the_provider() -> None:
    provider = _FakeProvider("anything")
    writer = TurnAchievementWriter(_FakeRegistry(provider), timeout_s=1.0)  # type: ignore[arg-type]
    assert await writer.write(request="   ") == DEFAULT_ACHIEVEMENT
    assert provider.calls == []


# =========================================================================== #
# 4. Integrity: it must never be able to see the answer
# =========================================================================== #


def test_the_writer_cannot_see_the_answer() -> None:
    """Structural, not a promise in a docstring.

    A criterion written after seeing the result can be retrofitted to whatever
    happened, which is exactly the failure this replaces. The signature is the
    guarantee.
    """
    import inspect

    params = set(inspect.signature(TurnAchievementWriter.write).parameters) - {"self"}
    assert params == {"request"}, f"write() can see more than the request: {params}"


@pytest.mark.asyncio
async def test_the_criterion_is_length_bounded() -> None:
    writer = _make("picture " * 500)
    got = await writer.write(request="Give me in pictures")
    assert len(got) <= TurnAchievementWriter.MAX_CRITERION_CHARS
