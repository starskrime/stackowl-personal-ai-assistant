"""FR-2 — non-format (content/tone/length) feedback captured as durable
preference NOTES, independent of the FORMAT/output_style path in
``test_feedback_capture.py``.

Covers: (a) a confident tone/length/content signal with referent=last writes a
note that then appears in the next turn's ``classify._gather_preferences``
output; (b) a low-confidence/abstain signal writes nothing; (c) the
FIFO-cap-at-20 + same-aspect-replace behavior of the underlying
``memory.preferences.write_preference_note`` helper.
"""

from __future__ import annotations

import pytest

from stackowl.interaction.feedback_classifier import FeedbackResult, FeedbackSignal
from stackowl.memory.preferences import (
    MAX_PREFERENCE_NOTES,
    PREFERENCE_NOTES_KEY,
    write_preference_note,
)
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import classify, feedback
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
    """Returns a fixed FeedbackResult regardless of input."""

    def __init__(self, result: FeedbackResult) -> None:
        self.result = result

    async def classify(self, *, user_message: str, last_agent_message: str,
                       recent_context: str | None = None) -> FeedbackResult:
        return self.result


def _result(*signals: FeedbackSignal, referent: str = "last",
            abstain: bool = False) -> FeedbackResult:
    return FeedbackResult(signals=tuple(signals), referent=referent,  # type: ignore[arg-type]
                          abstain=abstain, reason="test")


def _state(input_text: str, render: str, *, store: FakeStore, classifier: object) -> tuple[PipelineState, StepServices]:
    services = StepServices(preference_store=store)  # type: ignore[arg-type]
    services.feedback_classifier = classifier  # type: ignore[assignment]
    state = PipelineState(
        trace_id="t-fb", session_id="sess-fb", input_text=input_text,
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


# --------------------------------------------------------------------------- #
# (a) confident tone/length/content + referent=last → note written + surfaced #
# --------------------------------------------------------------------------- #

async def test_confident_length_signal_sets_output_style() -> None:
    """"length" is mechanically enforceable (the delivery-seam terse summarizer)
    — it now writes output_style like "format" does, NOT a free-text note
    nobody re-reads (see test_feedback_capture.py for the parallel format
    coverage)."""
    from stackowl.channels._format import OUTPUT_STYLE_KEY

    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="length", confidence=0.9)))
    state, services = _state("be more concise please", "A long reply here.",
                             store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, OUTPUT_STYLE_KEY) in store.data
    assert '"length": "terse"' in store.data[(OWNER, OUTPUT_STYLE_KEY)]
    assert (OWNER, PREFERENCE_NOTES_KEY) not in store.data  # no redundant note
    assert out.feedback_handled is True  # short-circuits like format does


async def test_note_appears_in_next_turn_prefs_block() -> None:
    """(a) — the written note is surfaced by classify._gather_preferences."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="positive", aspect="tone", confidence=0.95)))
    state, services = _state("I love how casual that sounded", "Hey, sup!",
                             store=store, classifier=classifier)
    token = set_services(services)
    try:
        await _run_and_join(state)
        block = await classify._gather_preferences(OWNER)
    finally:
        reset_services(token)
    assert "## Learned Preferences" in block
    assert "tone" in block
    assert "I love how casual that sounded" in block
    assert "{" not in block  # never a raw JSON dump


async def test_content_and_format_signals_both_write_independently() -> None:
    """(f) "good content but lose the asterisks" writes BOTH output_style and a note."""
    from stackowl.channels._format import OUTPUT_STYLE_KEY

    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(
            FeedbackSignal(polarity="positive", aspect="content", confidence=0.9),
            FeedbackSignal(polarity="negative", aspect="format", confidence=0.9),
        ))
    state, services = _state("good content but lose the asterisks",
                             "Here is **bold** text.", store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, OUTPUT_STYLE_KEY) in store.data  # format path still fired
    assert (OWNER, PREFERENCE_NOTES_KEY) in store.data  # note path fired too
    assert out.feedback_handled is True  # format path short-circuits as before


# --------------------------------------------------------------------------- #
# (b) low-confidence / abstain → no write                                    #
# --------------------------------------------------------------------------- #

async def test_abstain_signal_writes_no_note() -> None:
    """"content" stays a note-only aspect (unlike "length", which is now
    mechanically enforceable and — like "format" — asks a clarifying question
    on abstain instead of silently passing through; see
    test_feedback_capture.py::test_abstain_surfaces_question_no_write)."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="content", confidence=0.3),
                abstain=True))
    state, services = _state("that wasn't quite right", "A reply here.",
                             store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, PREFERENCE_NOTES_KEY) not in store.data
    assert out.feedback_handled is False  # no fmt signal → byte-identical pass-through


async def test_overall_aspect_writes_no_note() -> None:
    """"overall" is deliberately excluded — too vague to be an enforceable preference."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="positive", aspect="overall", confidence=0.95)))
    state, services = _state("nice", "A reply.", store=store, classifier=classifier)
    token = set_services(services)
    try:
        await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, PREFERENCE_NOTES_KEY) not in store.data


# --------------------------------------------------------------------------- #
# (c) FIFO cap-at-20 + same-aspect-replace                                   #
# --------------------------------------------------------------------------- #

async def test_same_aspect_replaces_not_duplicates() -> None:
    store = FakeStore()
    await write_preference_note(store, OWNER, aspect="length", polarity="negative", text="too long v1")
    notes = await write_preference_note(store, OWNER, aspect="length", polarity="negative", text="too long v2")
    assert len(notes) == 1
    assert notes[0]["text"] == "too long v2"  # newest wins, no duplicate


async def test_fifo_cap_at_20_evicts_oldest() -> None:
    store = FakeStore()
    for i in range(MAX_PREFERENCE_NOTES + 5):
        notes = await write_preference_note(
            store, OWNER, aspect=f"aspect-{i}", polarity="positive", text=f"note {i}")
    assert len(notes) == MAX_PREFERENCE_NOTES
    aspects = [n["aspect"] for n in notes]
    assert "aspect-0" not in aspects  # oldest evicted
    assert f"aspect-{MAX_PREFERENCE_NOTES + 4}" in aspects  # newest retained
