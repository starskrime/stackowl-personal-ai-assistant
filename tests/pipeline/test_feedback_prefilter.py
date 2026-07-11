"""FR-8 (de-complication PRD) — feedback classifier length pre-filter.

A message >= `feedback._PREFILTER_MAX_CHARS` (200) chars is a new task, not a
reaction to the prior render — the classifier LLM call must not fire at all.
Short messages must still classify exactly as before.
"""

from __future__ import annotations

import pytest

from stackowl.interaction.feedback_classifier import FeedbackResult, FeedbackSignal
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import feedback
from stackowl.providers.base import Message

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


class ScriptedClassifier:
    """Returns a fixed FeedbackResult; records whether/how it was called."""

    def __init__(self, result: FeedbackResult) -> None:
        self.result = result
        self.calls = 0

    async def classify(self, *, user_message: str, last_agent_message: str,
                       recent_context: str | None = None) -> FeedbackResult:
        self.calls += 1
        return self.result


def _result(*signals: FeedbackSignal, referent: str = "last",
            abstain: bool = False) -> FeedbackResult:
    return FeedbackResult(signals=tuple(signals), referent=referent,  # type: ignore[arg-type]
                          abstain=abstain, reason="test")


def _state(input_text: str, render: str, *, store: FakeStore,
           classifier: object) -> tuple[PipelineState, object]:
    services = StepServices(preference_store=store, db_pool=None)  # type: ignore[arg-type]
    services.feedback_classifier = classifier  # type: ignore[assignment]
    state = PipelineState(
        trace_id="t-fb-prefilter", session_id="sess-fb", input_text=input_text,
        channel="telegram", owl_name="secretary", pipeline_step="feedback",
        identity_key=OWNER,
        history=(Message(role="assistant", content=render),),
    )
    return state, services


async def _run_and_join(state: PipelineState) -> PipelineState:
    """LAT.3 — feedback.run() now only STARTS classification as a concurrent
    task; join it here so these tests observe the same final state run()
    returned synchronously before that story."""
    out = await feedback.run(state)
    if out.feedback_classify_task is not None:
        out = await out.feedback_classify_task
    return out


async def test_long_message_skips_classifier_byte_identical() -> None:
    """>= 200 chars → classifier.classify is never called; state passes through
    unchanged. LAT.3 — asserted directly on feedback.run()'s (unjoined) return so
    this proves the concurrent classify TASK is never even CREATED for this
    guard, not merely that nothing awaited it."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    long_text = "x" * 200
    assert len(long_text) >= feedback._PREFILTER_MAX_CHARS
    state, services = _state(long_text, "Here is **bold** text.",
                             store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await feedback.run(state)
    finally:
        reset_services(token)
    assert out.feedback_classify_task is None  # LAT.3 — no task started
    assert classifier.calls == 0
    assert out is state  # byte-identical pass-through
    assert store.data == {}


async def test_no_prior_render_guard_skips_without_creating_task() -> None:
    """No prior assistant render (first turn) → the same byte-identical
    pass-through guard as the long-message case above, and — LAT.3 — no
    classify task is ever created for it either."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    services = StepServices(preference_store=store, db_pool=None)  # type: ignore[arg-type]
    services.feedback_classifier = classifier  # type: ignore[assignment]
    state = PipelineState(
        trace_id="t-fb-no-render", session_id="sess-fb", input_text="nice",
        channel="telegram", owl_name="secretary", pipeline_step="feedback",
        identity_key=OWNER,
        # no history → no prior assistant render to react to
    )
    token = set_services(services)
    try:
        out = await feedback.run(state)
    finally:
        reset_services(token)
    assert out.feedback_classify_task is None  # LAT.3 — no task started
    assert classifier.calls == 0
    assert out is state  # byte-identical pass-through


async def test_short_message_still_invokes_classifier() -> None:
    """< 200 chars → the existing classify path still runs exactly as before."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    short_text = "x" * 199
    state, services = _state(short_text, "Here is **bold** text.",
                             store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert classifier.calls == 1
    assert out.feedback_handled is True
