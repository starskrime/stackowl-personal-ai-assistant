"""LAT.3 — feedback classification runs CONCURRENTLY with execute's answer-prep
instead of blocking in front of it.

Covers the concurrency-specific behavior that ``test_feedback_capture.py`` /
``test_feedback_prefilter.py`` / ``test_feedback_preference_notes.py`` don't:
  * a confident reaction still short-circuits correctly END-TO-END through
    ``execute.run`` (not just ``feedback.run`` in isolation);
  * a normal turn's wall-clock time is max(classify, prep), not the sum —
    the core regression guard proving real concurrency, not just unchanged
    behavior;
  * an abandoned task (an execute.py exit path that skips the join point) is
    explicitly cancelled and logged — never an untracked orphan.

The classify-guard-skip cases (no prior render / long message never even
CREATE a task) are covered in ``test_feedback_prefilter.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from stackowl.interaction.feedback_classifier import FeedbackResult, FeedbackSignal
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import execute as exe
from stackowl.pipeline.steps import feedback
from stackowl.providers.base import Message
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry

pytestmark = pytest.mark.asyncio

OWNER = "telegram:42"


class FakeStore:
    """In-memory PreferenceStore double keyed by (owner_key, key)."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    async def get(self, owner_key: str, key: str) -> str | None:
        return self.data.get((owner_key, key))

    async def set(self, owner_key: str, key: str, value: str) -> None:
        self.data[(owner_key, key)] = value

    async def list_for_owner(self, owner_key: str) -> dict[str, str]:
        return {k[1]: v for k, v in self.data.items() if k[0] == owner_key}


class DelayedClassifier:
    """Returns a fixed FeedbackResult after an optional controllable delay —
    the "answer-prep" side of the max()-not-sum() regression guard."""

    def __init__(self, result: FeedbackResult, *, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.calls = 0

    async def classify(self, *, user_message: str, last_agent_message: str,
                       recent_context: str | None = None) -> FeedbackResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _result(*signals: FeedbackSignal, referent: str = "last",
            abstain: bool = False) -> FeedbackResult:
    return FeedbackResult(signals=tuple(signals), referent=referent,  # type: ignore[arg-type]
                          abstain=abstain, reason="test")


# --------------------------------------------------------------------------- #
# execute.py provider-registry doubles (mirrors test_execute_conversational_notools.py)
# --------------------------------------------------------------------------- #


class _StreamingProvider:
    """Provider whose stream() yields a token — the plain-stream (no-tools) path a
    conversational reaction turn actually takes."""

    protocol = "anthropic"

    async def stream(self, messages: list[Any], model: str, **kwargs: object) -> Any:
        yield "hello"

    async def complete_with_tools(self, **kwargs: object) -> tuple[str, list[dict[str, Any]]]:
        return ("should not be called", [])  # pragma: no cover


class _FakeProviderRegistry:
    """``get()`` never raises → resolved as the owl-named-provider pin (step 0 of
    ``select_tool_provider_plan``) so no tier/session lookups are needed."""

    def __init__(self, provider: _StreamingProvider) -> None:
        self._p = provider

    def get(self, name: str) -> _StreamingProvider:
        return self._p

    def get_by_tier(self, tier: str) -> _StreamingProvider:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _StreamingProvider:
        return self._p


def _make_services(*, classifier: object, store: FakeStore) -> StepServices:
    services = StepServices(
        preference_store=store,  # type: ignore[arg-type]
        provider_registry=_FakeProviderRegistry(_StreamingProvider()),  # type: ignore[arg-type]
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    services.feedback_classifier = classifier  # type: ignore[assignment]
    return services


def _reaction_state(input_text: str, render: str) -> PipelineState:
    return PipelineState(
        trace_id="t-lat3", session_id="sess-lat3", input_text=input_text,
        channel="cli", owl_name="secretary", pipeline_step="feedback",
        identity_key=OWNER, intent_class="conversational",
        history=(Message(role="assistant", content=render),),
    )


# --------------------------------------------------------------------------- #
# 1. Confident reaction still short-circuits END-TO-END through execute.run    #
# --------------------------------------------------------------------------- #


async def test_confident_reaction_short_circuits_through_execute() -> None:
    """feedback.run() starts the task (non-blocking); execute.run() must join it
    and skip the tool loop / generation exactly as the old synchronous
    feedback.run() short-circuit did (AC#2)."""
    store = FakeStore()
    classifier = DelayedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    services = _make_services(classifier=classifier, store=store)
    state = _reaction_state("no, drop the asterisks", "Here is **bold** text.")

    token = set_services(services)
    try:
        after_feedback = await feedback.run(state)
        # LAT.3 — feedback.run() must NOT have blocked on the classify call.
        assert after_feedback.feedback_classify_task is not None
        assert after_feedback.feedback_handled is False  # not yet known

        final = await exe.run(after_feedback)
    finally:
        reset_services(token)

    assert final.feedback_handled is True
    assert final.feedback_classify_task is None  # consumed at the join
    text = "".join(c.content for c in final.responses)
    assert "asterisks" in text  # the plain-language confirmation, not a generated answer


# --------------------------------------------------------------------------- #
# 1b. Confident reaction wins even when EVERY provider is down                #
# --------------------------------------------------------------------------- #


def _open_breaker(reg: ProviderRegistry, name: str) -> None:
    for _ in range(3):
        reg._breakers[name]._record_failure()


async def test_confident_reaction_wins_over_all_providers_unavailable() -> None:
    """The gather races _resolve_provider_choice (which floors to an error
    PipelineState via AllProvidersUnavailableError) against the feedback join.
    A handled reaction never needed a provider at all, so it must win over the
    provider-outage error — this is the exact intersection the check-order
    reorder in execute.run() fixes (pre-fix: the provider error was returned
    first, discarding the confirmation)."""
    store = FakeStore()
    classifier = DelayedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    reg = ProviderRegistry()
    reg.register_mock("powerful_a", MockProvider(name="powerful_a"), tier="powerful")
    _open_breaker(reg, "powerful_a")  # only provider OPEN → AllProvidersUnavailableError
    services = StepServices(
        preference_store=store,  # type: ignore[arg-type]
        provider_registry=reg,
        owl_registry=OwlRegistry.with_default_secretary(),
    )
    services.feedback_classifier = classifier  # type: ignore[assignment]
    state = _reaction_state("no, drop the asterisks", "Here is **bold** text.")

    token = set_services(services)
    try:
        after_feedback = await feedback.run(state)
        assert after_feedback.feedback_classify_task is not None

        final = await exe.run(after_feedback)
    finally:
        reset_services(token)

    assert final.feedback_handled is True
    assert not any("AllProvidersUnavailableError" in e for e in final.errors), (
        "provider outage must not mask a confirmed reaction that needed no provider"
    )
    text = "".join(c.content for c in final.responses)
    assert "asterisks" in text  # the confirmation, not a provider-outage floor


# --------------------------------------------------------------------------- #
# 2. Wall-clock: max(classify, prep), not the sum                             #
# --------------------------------------------------------------------------- #


async def test_wall_clock_reflects_max_not_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    """classify and execute's own provider-choice prep are mocked to
    deterministic, comparable durations. If execute.run() still serially awaited
    classify BEFORE its own prep (the pre-LAT.3 shape), total time would be
    close to classify_delay + prep_delay. Concurrent (correct) execution keeps
    total time close to max(classify_delay, prep_delay)."""
    store = FakeStore()
    classify_delay = 0.20
    prep_delay = 0.20
    classifier = DelayedClassifier(
        # A non-feedback verdict — the turn proceeds to normal generation, so the
        # timing reflects the join + prep path, not the short-circuit return.
        _result(FeedbackSignal(polarity="neutral", aspect="overall", confidence=0.9),
                referent="none"),
        delay=classify_delay,
    )
    services = _make_services(classifier=classifier, store=store)
    state = _reaction_state("what's the weather", "A previous reply.")

    async def _slow_prep(*_a: object, **_k: object) -> Any:
        await asyncio.sleep(prep_delay)
        return await real_resolve_provider_choice(*_a, **_k)

    real_resolve_provider_choice = exe._resolve_provider_choice
    monkeypatch.setattr(exe, "_resolve_provider_choice", _slow_prep)

    token = set_services(services)
    try:
        t0 = time.monotonic()
        after_feedback = await feedback.run(state)
        final = await exe.run(after_feedback)
        elapsed = time.monotonic() - t0
    finally:
        reset_services(token)

    assert final.feedback_classify_task is None  # joined
    sum_time = classify_delay + prep_delay
    max_time = max(classify_delay, prep_delay)
    # Generous slack for scheduler jitter — the assertion that matters is
    # "closer to max than to sum", not a tight bound.
    assert elapsed < sum_time - 0.08, (
        f"elapsed={elapsed:.3f}s not clearly under the SERIAL sum "
        f"({sum_time:.3f}s) — classify no longer ran concurrently with prep"
    )
    assert elapsed < max_time + 0.15, (
        f"elapsed={elapsed:.3f}s far exceeds max(classify, prep)={max_time:.3f}s"
    )


# --------------------------------------------------------------------------- #
# 3. Abandonment — an exit path that skips the join must cancel + log         #
# --------------------------------------------------------------------------- #


async def test_abandoned_task_is_cancelled_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """execute.run() with NO provider_registry returns before the join point
    (AC#5). The in-flight classify task must be explicitly cancelled — never
    left an untracked orphan — with a clear log line."""
    store = FakeStore()
    never_resolves = asyncio.Event()

    class HangingClassifier:
        calls = 0

        async def classify(self, *, user_message: str, last_agent_message: str,
                           recent_context: str | None = None) -> FeedbackResult:
            HangingClassifier.calls += 1
            await never_resolves.wait()  # never set — simulates a still-in-flight call
            raise AssertionError("unreachable")  # pragma: no cover

    services = StepServices(preference_store=store)  # type: ignore[arg-type]
    services.feedback_classifier = HangingClassifier()  # type: ignore[assignment]
    # provider_registry left None → execute.run() takes the early-return path.
    state = _reaction_state("no, drop the asterisks", "Here is **bold** text.")

    token = set_services(services)
    try:
        after_feedback = await feedback.run(state)
        task = after_feedback.feedback_classify_task
        assert task is not None and not task.done()

        with caplog.at_level(logging.WARNING, logger="stackowl.engine"):
            final = await exe.run(after_feedback)
    finally:
        reset_services(token)

    # Give the cancellation a turn of the loop to land.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert final.feedback_handled is False
    assert any(
        "feedback classify task abandoned" in r.message for r in caplog.records
    ), "expected an explicit abandonment log line, found none"
