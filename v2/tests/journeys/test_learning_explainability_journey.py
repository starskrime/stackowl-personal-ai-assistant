"""Learning Explainability Journey — end-to-end proof of pillar ④.

Drives the REAL AsyncioBackend pipeline with a scripted provider that calls
``note_applied_lesson`` via the REAL ``tool_dispatcher``. After the turn the
render step (``surface_applied_lessons``) must append the explanation line to
the user-visible response — so the DELIVERED text contains BOTH the model's
final answer AND the applied-lesson explanation derived from ``what_you_did``.

No LanceDB, no embedder, no lesson index is needed: the tool records directly
into the turn-scoped ``lesson_context`` carrier (wired by the backend at
turn-start via ``lc.bind()``); the render step reads from that carrier and
builds the user line from ``what_you_did`` alone.

Mirrors the harness pattern of ``test_j4_tools_bounds.py`` (same scripted
provider shape, same ``_FakeProviderRegistry``, same direct ``backend.run()``
result-capture as ``test_self_heal_substitution.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult, Message
from stackowl.providers.react_callback import IterationCallback
from stackowl.tools.meta.note_applied_lesson import NoteAppliedLessonTool
from stackowl.tools.registry import ConsequentialActionGate, ToolRegistry

_FINAL_ANSWER = "Here is the summary you asked for."
_WHAT_YOU_DID = "used fetch instead of browse_url"


# ---------------------------------------------------------------------------
# Scripted provider — mirrors _ScriptedBoundedOwl from test_j4_tools_bounds
# ---------------------------------------------------------------------------


class _ScriptedLearningOwl:
    """The ONLY mock. Calls ``note_applied_lesson`` via the real tool_dispatcher,
    then returns a final answer — exercising the pillar ④ self-report path."""

    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: object,
        tool_dispatcher: object,
        history: object = None,
        **_kw: object,
    ) -> tuple[str, list[object]]:
        # Call the real tool via the real dispatcher — records into lesson_context
        await tool_dispatcher(  # type: ignore[operator]
            "note_applied_lesson",
            {"lesson_id": "L1", "what_you_did": _WHAT_YOU_DID},
        )
        return (_FINAL_ANSWER, [])

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="I'll summarize that for you.",
            input_tokens=4,
            output_tokens=6,
            model="learning-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *_a: object, **_kw: object):  # pragma: no cover
        if False:
            yield ""


class _FakeProviderRegistry:
    def __init__(self, provider: _ScriptedLearningOwl) -> None:
        self._p = provider

    def get(self, name: str) -> _ScriptedLearningOwl:
        return self._p

    def get_by_tier(self, tier: str) -> _ScriptedLearningOwl:
        return self._p

    def get_with_cascade(self, preferred_tier: str) -> _ScriptedLearningOwl:
        return self._p


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io() -> object:  # type: ignore[return]
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _build_backend() -> AsyncioBackend:
    registry = ToolRegistry()
    registry.register(NoteAppliedLessonTool())

    owl_registry = OwlRegistry.with_default_secretary()

    services = StepServices(
        provider_registry=_FakeProviderRegistry(_ScriptedLearningOwl()),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        owl_registry=owl_registry,
    )
    return AsyncioBackend(services=services)


async def _run_turn(backend: AsyncioBackend, text: str) -> str:
    scanner = GatewayScanner(owl_registry=OwlRegistry.with_default_secretary())
    msg = IngressMessage(
        text=text,
        session_id="sess-learning-explainability",
        channel="cli",
        trace_id="trace-learning-explainability-1",
    )
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=True,
    )
    final_state = await backend.run(state)
    return "".join(c.content for c in final_state.responses)


# ---------------------------------------------------------------------------
# The journey test
# ---------------------------------------------------------------------------


async def test_applied_lesson_explanation_reaches_user() -> None:
    """When the model calls note_applied_lesson and the backend is wired,
    the delivered response must contain BOTH the final answer AND the
    applied-lesson explanation line (derived from what_you_did)."""
    backend = _build_backend()
    delivered = await _run_turn(backend, "please summarize the document")

    # OUTCOME 1 — the model's final answer is present
    assert _FINAL_ANSWER in delivered, (
        f"Final answer missing from delivered text. Got: {delivered!r}"
    )

    # OUTCOME 2 — the applied-lesson explanation line is present; it is derived
    # from what_you_did="used fetch instead of browse_url" via localize_format
    # → "ℹ️ I drew on something I learned: used fetch instead of browse_url"
    assert _WHAT_YOU_DID in delivered, (
        f"Applied-lesson explanation (what_you_did) missing from delivered text. "
        f"Got: {delivered!r}"
    )


# ---------------------------------------------------------------------------
# Negative-scenario providers
# ---------------------------------------------------------------------------


class _ScriptedNoLessonOwl:
    """Scripted provider that returns a plain answer WITHOUT calling note_applied_lesson."""

    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: object,
        tool_dispatcher: object,
        history: object = None,
        **_kw: object,
    ) -> tuple[str, list[object]]:
        # Deliberately does NOT call note_applied_lesson — no lesson was used
        return ("Plain answer, no lesson used.", [])

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="Plain answer, no lesson used.",
            input_tokens=4,
            output_tokens=6,
            model="no-lesson-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *_a: object, **_kw: object):  # pragma: no cover
        if False:
            yield ""


class _ScriptedBogusIdOwl:
    """Scripted provider that calls note_applied_lesson with an unknown lesson id."""

    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(  # noqa: ANN001
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: object,
        tool_dispatcher: object,
        history: object = None,
        **_kw: object,
    ) -> tuple[str, list[object]]:
        # L99 was never surfaced this turn — unknown id
        await tool_dispatcher(  # type: ignore[operator]
            "note_applied_lesson",
            {"lesson_id": "L99", "what_you_did": "applied a vague intuition"},
        )
        return ("Answer with a bogus citation.", [])

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        return CompletionResult(
            content="Answer with a bogus citation.",
            input_tokens=4,
            output_tokens=6,
            model="bogus-id-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *_a: object, **_kw: object):  # pragma: no cover
        if False:
            yield ""


# ---------------------------------------------------------------------------
# Negative journey tests
# ---------------------------------------------------------------------------


async def test_no_claim_when_tool_not_called() -> None:
    """FR3 (no overclaim): if the model never calls note_applied_lesson, the
    delivered response must contain no learning/avoidance claim."""
    registry = ToolRegistry()
    registry.register(NoteAppliedLessonTool())
    owl_registry = OwlRegistry.with_default_secretary()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_ScriptedNoLessonOwl()),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        owl_registry=owl_registry,
    )
    backend = AsyncioBackend(services=services)
    delivered = await _run_turn(backend, "give me a plain answer")

    # OUTCOME 1 — the plain answer IS present
    assert "Plain answer, no lesson used." in delivered, (
        f"Expected plain answer in delivered text. Got: {delivered!r}"
    )

    # OUTCOME 2 — no learning claim must appear (FR3 honesty invariant)
    assert "I drew on something I learned" not in delivered, (
        f"Overclaim detected: learning claim present even though note_applied_lesson "
        f"was never called. Got: {delivered!r}"
    )
    assert "ℹ️" not in delivered, (
        f"Overclaim detected: ℹ️ marker present even though note_applied_lesson "
        f"was never called. Got: {delivered!r}"
    )


async def test_unknown_id_does_not_error_and_uses_what_you_did() -> None:
    """FR4 (graceful id mismatch): citing an unknown lesson id must not error,
    and the explanation must derive from what_you_did regardless of id resolution."""
    registry = ToolRegistry()
    registry.register(NoteAppliedLessonTool())
    owl_registry = OwlRegistry.with_default_secretary()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_ScriptedBogusIdOwl()),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        owl_registry=owl_registry,
    )
    backend = AsyncioBackend(services=services)
    delivered = await _run_turn(backend, "give me an answer citing a lesson")

    # OUTCOME 1 — the turn completed (no exception raised) and the answer IS present
    assert "Answer with a bogus citation." in delivered, (
        f"Expected answer in delivered text. Got: {delivered!r}"
    )

    # OUTCOME 2 — what_you_did IS present in the explanation (fallback from id mismatch)
    assert "applied a vague intuition" in delivered, (
        f"Expected what_you_did fallback in delivered text when lesson id is unknown. "
        f"Got: {delivered!r}"
    )


# ---------------------------------------------------------------------------
# Critical-failure + applied-lesson honesty test
# ---------------------------------------------------------------------------


_APOLOGY_TEXT = "Sorry, I could not complete your request right now."


class _LessonThenCrashOwl:
    """Scripted provider that calls ``note_applied_lesson`` then RAISES, causing a
    critical execute-step failure with no usable response. Its ``complete`` SUCCEEDS
    (returns a localized apology), so ``surface_critical_failure`` injects that apology
    as a ResponseChunk with ``is_floor=False`` — this is the exact chunk the bug
    mistakes for a real answer, causing a false learning claim."""

    protocol = "anthropic"

    @property
    def name(self) -> str:
        return "secretary"

    async def complete_with_tools(
        self,
        *,
        user_text: str,
        system_text: str,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_iterations: int = 8,
        history: list[Any] | None = None,
        persistence_check: Callable[[str, list[str]], Awaitable[str | None]] | None = None,
        on_iteration_complete: IterationCallback | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
        resume_tool_calls: list[dict[str, Any]] | None = None,
        **_kw: object,
    ) -> tuple[str, list[object]]:
        # Record the lesson FIRST, then crash — this is the honesty bug scenario:
        # the lesson was "applied" but the turn then critically failed with no answer.
        await tool_dispatcher(
            "note_applied_lesson",
            {"lesson_id": "L1", "what_you_did": "tried the learned shortcut"},
        )
        raise RuntimeError("provider crashed after lesson note (simulated outage)")

    async def complete(self, *_a: object, **_kw: object) -> CompletionResult:
        # The apology cascade calls this and it SUCCEEDS — returning a localized apology.
        # surface_critical_failure injects this as a ResponseChunk with is_floor=False.
        # PRE-FIX: surface_applied_lessons then sees this is_floor=False chunk and
        # incorrectly treats it as a "real answer", appending the learning claim.
        # POST-FIX: surface_applied_lessons runs BEFORE surface_critical_failure, so
        # on the failed turn there is no apology chunk yet and the guard fires correctly.
        return CompletionResult(
            content=_APOLOGY_TEXT,
            input_tokens=4,
            output_tokens=12,
            model="apology-model",
            provider_name="secretary",
            duration_ms=1.0,
        )

    async def stream(self, *_a: object, **_kw: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


async def test_no_learning_claim_on_critically_failed_turn() -> None:
    """HONESTY GATE: note_applied_lesson was called this turn but the turn ended
    in a CRITICAL execute-step failure with no real answer. The delivered text
    must NOT contain a learning claim — even though the lesson tool was called.

    Pre-fix (wrong order): surface_critical_failure injects the apology chunk
    (is_floor=False), which the has_real_answer guard in surface_applied_lessons
    mistook for a real answer, causing a false learning claim on a failed turn.
    Post-fix (correct order): surface_applied_lessons runs BEFORE
    surface_critical_failure; on a failed turn responses are empty / floor-only
    at that point, so the guard correctly suppresses the annotation.
    """
    registry = ToolRegistry()
    registry.register(NoteAppliedLessonTool())
    owl_registry = OwlRegistry.with_default_secretary()
    services = StepServices(
        provider_registry=_FakeProviderRegistry(_LessonThenCrashOwl()),  # type: ignore[arg-type]
        tool_registry=registry,
        consent_gate=ConsequentialActionGate(),
        owl_registry=owl_registry,
        stream_registry=StreamRegistry(),
    )
    backend = AsyncioBackend(services=services)
    delivered = await _run_turn(backend, "please do the task that uses a learned shortcut")

    # OUTCOME — no learning claim must appear even though note_applied_lesson was called.
    # The turn critically failed; appending "I drew on something I learned" onto a
    # failure apology / floor is a false success claim and must be suppressed.
    assert "I drew on something I learned" not in delivered, (
        f"HONESTY BUG: learning claim appeared on a critically-failed turn. "
        f"Got: {delivered!r}"
    )
    assert "ℹ️" not in delivered, (
        f"HONESTY BUG: ℹ️ marker appeared on a critically-failed turn. "
        f"Got: {delivered!r}"
    )
