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

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message
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
