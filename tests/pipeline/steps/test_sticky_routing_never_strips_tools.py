"""A sticky-routed follow-up may inherit the OWL. It may never inherit tool-freedom.

EARNED 2026-08-31, live and user-visible. Bakir asked "Explain me in easy way how
to remember bfs for tree in python" — routed ``conversational`` and cached. Then
he asked **"Give me in pictures"**: 19 characters, same session, so FR-9's
mechanical bypass fired and the turn inherited ``conversational`` WITHOUT BEING
CLASSIFIED AT ALL. ``conversational`` is in ``TOOL_FREE_CLASSES``, so the turn ran
``tools_used=false`` with no tools presented. The model, unable to draw, replied
"I'll draw this as an actual image for you." No image ever came, and the task
closed ``completed``.

THE 2026-07-01 ADVERSARIAL REVIEW GOT THE ASYMMETRY BACKWARDS, and its reasoning
is quoted in triage.py. It banned caching ``standard`` because a stale work-turn
resolution "silently defeats the F120 tool-capability gate". That is the right
worry pointed at the wrong class:

  * a wrong ``standard`` costs a possibly-wrong owl, and the tools are still there;
  * a wrong ``conversational`` REMOVES THE TOOLS. It is the only cached class that
    can strip capability, and it is the one they kept.

Its stated safety case — "only the low-risk conversational-follow-up case ('ok
thanks', 'sounds good') is fast-pathed" — assumes a short follow-up is never a new
task. ``_STICKY_MAX_CHARS`` is 200, which comfortably fits "make me a chart of my
applications and send it".

WHAT IS KEPT AND WHAT IS DROPPED. The bypass exists to skip an LLM router call,
and that saving is entirely in reusing the OWL. Reusing the tool-free class saves
nothing extra and is what cost the turn. So the owl is still inherited, the router
is still not called, and the turn is no longer born unable to act.
"""

from __future__ import annotations

import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.sticky_route_cache import StickyRouteCache
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import TOOL_FREE_CLASSES, PipelineState
from stackowl.pipeline.steps import triage as triage_step
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t", session_key="s", input_text="hi", owl_name="secretary",
        channel="cli", pipeline_step="start",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _build(canned: str = "research_owl\nconversational") -> tuple[StepServices, MockProvider]:
    registry = OwlRegistry.with_default_secretary()
    registry.register(
        OwlAgentManifest(
            name="research_owl", role="generic", system_prompt="Be helpful.", model_tier="fast"
        )
    )
    mock = MockProvider(name="router-mock", canned_text=canned)
    preg = ProviderRegistry()
    preg.register_mock(mock.name, mock, tier="fast")
    return (
        StepServices(
            provider_registry=preg, owl_registry=registry, sticky_route_cache=StickyRouteCache()
        ),
        mock,
    )


@pytest.mark.asyncio
async def test_the_live_incident_a_short_followup_is_not_born_toolless() -> None:
    """Replays 2026-08-31 03:30-03:32 exactly."""
    services, mock = _build()
    token = set_services(services)
    try:
        first = await triage_step.run(_state(
            session_key="sess-bfs",
            input_text="Explain me in easy way hiw to remember bfs for tree in python",
        ))
        assert first.intent_class == "conversational"
        assert mock.call_count == 1

        second = await triage_step.run(
            _state(session_key="sess-bfs", input_text="Give me in pictures")
        )
        assert second.intent_class not in TOOL_FREE_CLASSES, (
            "a sticky-routed follow-up inherited tool-freedom — this is the "
            "'Give me in pictures' failure"
        )
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_the_saving_the_bypass_exists_for_is_still_there() -> None:
    """The owl is still inherited and the router is still not called.

    If this regresses, the fix has thrown away the optimisation instead of its
    dangerous half.
    """
    services, mock = _build()
    token = set_services(services)
    try:
        first = await triage_step.run(_state(session_key="sess-keep", input_text="hey there"))
        assert first.owl_name == "research_owl"
        assert mock.call_count == 1

        second = await triage_step.run(_state(session_key="sess-keep", input_text="thanks!"))
        assert second.owl_name == "research_owl", "the owl was not inherited"
        assert mock.call_count == 1, "the router was called again — the saving is gone"
        assert second.intent_classified is True
        assert second.clarify_question is None
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_a_trivial_acknowledgement_also_keeps_its_tools() -> None:
    """The rule is unconditional, deliberately.

    Deciding per-message which short follow-up "is really a task" is the
    new-topic detection FR-9 explicitly does not have. Rather than guess, every
    sticky turn keeps its capability; the cost is tool schemas on an "ok thanks",
    and the alternative cost is an entire lost turn.
    """
    services, _mock = _build()
    token = set_services(services)
    try:
        await triage_step.run(_state(session_key="sess-ack", input_text="hey there"))
        second = await triage_step.run(_state(session_key="sess-ack", input_text="ok thanks"))
        assert second.intent_class not in TOOL_FREE_CLASSES
    finally:
        reset_services(token)


@pytest.mark.asyncio
async def test_a_directly_routed_conversational_turn_is_untouched() -> None:
    """Only the STICKY path changes.

    A turn the router itself classified conversational stays conversational —
    that classification looked at the message. This item is about inheriting a
    verdict for a message nobody classified.
    """
    services, mock = _build()
    token = set_services(services)
    try:
        first = await triage_step.run(_state(session_key="sess-direct", input_text="hey there"))
        assert first.intent_class == "conversational"
        assert mock.call_count == 1
    finally:
        reset_services(token)
