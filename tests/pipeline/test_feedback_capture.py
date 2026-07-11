"""LS4 — feedback capture step.

Asserts on the PREFERENCE STORE + the outcome row + the plain-language
confirmation — NEVER on model prose. The classifier is scripted (the LLM verdict
is LS3's job; LS4 consumes its :class:`FeedbackResult`). Covers the seven cases
in the LS4 spec: negative→markdown/links, positive pin, content aspect-scope
guard, abstain question, non-feedback byte-identical, confirmation wording.
"""

from __future__ import annotations

import json

import pytest

from stackowl.channels._format import OUTPUT_STYLE_KEY, OutputStyle, resolve_output_style
from stackowl.interaction.feedback_classifier import (
    FeedbackResult,
    FeedbackSignal,
)
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


class FakeDb:
    """Captures TaskOutcomeStore.record's INSERT so we can assert the row."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.inserts.append((sql, params))


class ScriptedClassifier:
    """Returns a fixed FeedbackResult regardless of input."""

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


def _state(render: str, *, store: FakeStore, classifier: object,
           db: object | None = None) -> tuple[PipelineState, object]:
    services = StepServices(preference_store=store, db_pool=db)  # type: ignore[arg-type]
    services.feedback_classifier = classifier  # type: ignore[assignment]
    state = PipelineState(
        trace_id="t-fb", session_id="sess-fb", input_text="...",
        channel="telegram", owl_name="secretary", pipeline_step="feedback",
        identity_key=OWNER,
        history=(Message(role="assistant", content=render),),
    )
    return state, services


def _stored_style(store: FakeStore, owner_key: str = OWNER) -> OutputStyle:
    return resolve_output_style(
        {k[1]: v for k, v in store.data.items() if k[0] == owner_key})


async def _run_and_join(state: PipelineState) -> PipelineState:
    """LAT.3 — feedback.run() now only STARTS classification as a concurrent
    task; join it here so these tests observe the same final state run()
    returned synchronously before that story."""
    out = await feedback.run(state)
    if out.feedback_classify_task is not None:
        out = await out.feedback_classify_task
    return out


# --------------------------------------------------------------------------- #

async def test_negative_format_asterisks_sets_markdown_minimal() -> None:
    """(a) negative/format about a render containing `*` → markdown=minimal + outcome row."""
    store, db = FakeStore(), FakeDb()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    state, services = _state("Here is **bold** text.", store=store, classifier=classifier, db=db)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert _stored_style(store).markdown == "minimal"
    assert out.feedback_handled is True
    # outcome row written as a rejection (success=0, failure_class set) — NOT a lesson
    assert len(db.inserts) == 1
    params = db.inserts[0][1]
    assert 0 in params  # success int(False) == 0
    assert "feedback_rejected" in params


async def test_negative_format_untitled_link_sets_links_titles() -> None:
    """(b) negative/format about a render with a bare link → links=titles."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    state, services = _state("See https://example.com/news for more.",
                             store=store, classifier=classifier, db=FakeDb())
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert _stored_style(store).links == "titles"
    assert out.feedback_handled is True


async def test_positive_format_pins_current_style() -> None:
    """(c) positive/format → the current effective style is persisted (pinned)."""
    store = FakeStore()
    # An existing explicit style (markdown minimal) is the effective style.
    store.data[(OWNER, OUTPUT_STYLE_KEY)] = json.dumps({"markdown": "minimal"})
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="positive", aspect="format", confidence=0.9)))
    state, services = _state("Clean reply, no markup.", store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert _stored_style(store).markdown == "minimal"  # pinned/durable
    assert out.feedback_handled is True


async def test_positive_format_no_prior_infers_clean_shape() -> None:
    """(c') positive/format with no prior style → infer clean attrs of the render."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="positive", aspect="format", confidence=0.9)))
    # Render is clean (no asterisks, no table, no bare link) → lock that shape.
    state, services = _state("Just plain prose here.", store=store, classifier=classifier)
    token = set_services(services)
    try:
        await _run_and_join(state)
    finally:
        reset_services(token)
    style = _stored_style(store)
    assert style.markdown == "minimal"
    assert style.tables == "off"
    assert style.links == "titles"


async def test_positive_content_only_writes_no_format_rule() -> None:
    """(d) positive on CONTENT only → NO output_style write (aspect-scope guard)."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="positive", aspect="content", confidence=0.9)))
    state, services = _state("**bold** answer with a table maybe.",
                             store=store, classifier=classifier, db=FakeDb())
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, OUTPUT_STYLE_KEY) not in store.data
    assert out.feedback_handled is False  # not short-circuited — normal turn proceeds


async def test_abstain_surfaces_question_no_write() -> None:
    """(e) abstain on a format reaction → a clarifying question, NO preference write."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.3),
                abstain=True))
    state, services = _state("Here is **bold** text.", store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert (OWNER, OUTPUT_STYLE_KEY) not in store.data  # no write
    assert out.feedback_handled is True  # short-circuited with a question
    text = "".join(c.content for c in out.responses)
    assert "?" in text


async def test_non_feedback_is_byte_identical() -> None:
    """(f) a non-feedback message (neutral / referent none) → no write, unchanged state."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="neutral", aspect="overall", confidence=0.9),
                referent="none"))
    state, services = _state("What is the weather today?", store=store, classifier=classifier)
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    assert store.data == {}  # nothing written
    assert out is state  # byte-identical: same object back
    assert out.feedback_handled is False


async def test_confirmation_is_plain_language_not_learned_prose() -> None:
    """(g) confirmation reads the rule back plainly; never "learned"/"protocol updated"."""
    store = FakeStore()
    classifier = ScriptedClassifier(
        _result(FeedbackSignal(polarity="negative", aspect="format", confidence=0.9)))
    state, services = _state("See **bold** and https://example.com/x here.",
                             store=store, classifier=classifier, db=FakeDb())
    token = set_services(services)
    try:
        out = await _run_and_join(state)
    finally:
        reset_services(token)
    text = "".join(c.content for c in out.responses).lower()
    assert "learned" not in text
    assert "protocol updated" not in text
    assert "asterisks" in text  # names the defect / the plain rule
