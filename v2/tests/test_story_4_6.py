"""Story 4.6 — A2A Messaging: Secretary-to-Specialist Delegation.

Tests cover:
  * A2AQueue HealthContributor (health_check, queue_depths)
  * A2AQueue regression — existing send/receive/timeout behavior
  * dispatch step routing (pass-through, unknown owl fallback, known owl)
  * A2ADelegator end-to-end round-trip with MockProvider
  * A2ADelegator timeout handling and trace_id preservation
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stackowl.exceptions import A2ATimeoutError
from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import dispatch
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(**overrides: Any) -> PipelineState:
    defaults: dict[str, Any] = {
        "trace_id": "trace-4-6",
        "session_id": "sess-4-6",
        "input_text": "hello",
        "channel": "cli",
        "owl_name": "secretary",
        "pipeline_step": "dispatch",
    }
    defaults.update(overrides)
    return PipelineState(**defaults)


def _make_manifest(name: str) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role="researcher",
        system_prompt="Be helpful.",
        model_tier="fast",
    )


def _make_registry_with(*owl_names: str) -> OwlRegistry:
    registry = OwlRegistry.with_default_secretary()
    for name in owl_names:
        registry.register(_make_manifest(name))
    return registry


def _make_provider_registry(*owl_names: str, canned: str = "specialist reply") -> ProviderRegistry:
    """Register one MockProvider per owl name so per-owl lookup hits."""
    registry = ProviderRegistry()
    for owl in owl_names:
        registry.register_mock(owl, MockProvider(name=owl, canned_text=canned), tier="fast")
    # Also register a fallback "powerful" mock for the execute step fallback path.
    registry.register_mock(
        "fallback-powerful",
        MockProvider(name="fallback-powerful", canned_text=canned),
        tier="powerful",
    )
    return registry


# ---------------------------------------------------------------------------
# A2AQueue HealthContributor
# ---------------------------------------------------------------------------


class TestA2AQueueHealth:
    async def test_health_ok_when_no_queues(self) -> None:
        queue = A2AQueue()
        status = await queue.health_check()
        assert status.status == "ok"
        assert status.name == "a2a_queue"
        assert status.message is None

    async def test_health_ok_when_all_below_threshold(self) -> None:
        queue = A2AQueue()
        for i in range(5):
            queue.send(
                A2AMessage.now(
                    from_owl="a",
                    to_owl="x",
                    content=str(i),
                    message_type="event",
                    trace_id="t",
                )
            )
        status = await queue.health_check()
        assert status.status == "ok"

    async def test_health_ok_when_exactly_at_threshold(self) -> None:
        queue = A2AQueue()
        for i in range(10):
            queue.send(
                A2AMessage.now(
                    from_owl="a",
                    to_owl="x",
                    content=str(i),
                    message_type="event",
                    trace_id="t",
                )
            )
        status = await queue.health_check()
        assert status.status == "ok"

    async def test_health_degraded_when_above_threshold(self) -> None:
        queue = A2AQueue()
        for i in range(11):
            queue.send(
                A2AMessage.now(
                    from_owl="a",
                    to_owl="x",
                    content=str(i),
                    message_type="event",
                    trace_id="t",
                )
            )
        status = await queue.health_check()
        assert status.status == "degraded"
        assert status.message is not None
        assert "11" in status.message

    async def test_queue_depths_returns_per_owl_counts(self) -> None:
        queue = A2AQueue()
        for i in range(3):
            queue.send(
                A2AMessage.now(
                    from_owl="s",
                    to_owl="owlA",
                    content=str(i),
                    message_type="event",
                    trace_id="t",
                )
            )
        for i in range(2):
            queue.send(
                A2AMessage.now(
                    from_owl="s",
                    to_owl="owlB",
                    content=str(i),
                    message_type="event",
                    trace_id="t",
                )
            )
        depths = queue.queue_depths()
        assert depths == {"owlA": 3, "owlB": 2}

    async def test_queue_depths_empty_initially(self) -> None:
        assert A2AQueue().queue_depths() == {}

    def test_contributor_name(self) -> None:
        assert A2AQueue().contributor_name == "a2a_queue"


# ---------------------------------------------------------------------------
# A2AQueue existing behavior (regression)
# ---------------------------------------------------------------------------


class TestA2AQueueRegression:
    async def test_send_receive_round_trip_still_works(self) -> None:
        queue = A2AQueue()
        msg = A2AMessage.now(
            from_owl="secretary",
            to_owl="research",
            content="lookup",
            message_type="request",
            trace_id="t-regress",
        )
        queue.send(msg)
        received = await queue.receive("research", timeout=1.0)
        assert received.content == "lookup"
        assert received.trace_id == "t-regress"

    async def test_timeout_raises_a2a_timeout_error(self) -> None:
        queue = A2AQueue()
        with pytest.raises(A2ATimeoutError) as exc_info:
            await queue.receive("ghost", timeout=0.05)
        assert exc_info.value.owl_name == "ghost"

    async def test_sent_at_property_returns_datetime(self) -> None:
        msg = A2AMessage.now(
            from_owl="a",
            to_owl="b",
            content="c",
            message_type="event",
            trace_id="t",
        )
        assert msg.sent_at.tzinfo is not None
        assert msg.sent_at.isoformat() == msg.timestamp


# ---------------------------------------------------------------------------
# Dispatch step
# ---------------------------------------------------------------------------


class TestDispatchStep:
    async def test_pass_through_when_no_a2a_queue(self) -> None:
        # No services set → get_services returns empty StepServices → pass-through.
        state = _state(owl_name="research")
        result = await dispatch.run(state)
        assert result.owl_name == "research"

    async def test_pass_through_when_target_is_secretary(self) -> None:
        from stackowl.pipeline.services import reset_services, set_services

        services = StepServices(
            owl_registry=_make_registry_with("research"),
            a2a_queue=A2AQueue(),
        )
        token = set_services(services)
        try:
            state = _state(owl_name="secretary")
            result = await dispatch.run(state)
            assert result.owl_name == "secretary"
        finally:
            reset_services(token)

    async def test_unknown_owl_falls_back_to_secretary(self) -> None:
        from stackowl.pipeline.services import reset_services, set_services

        services = StepServices(
            owl_registry=_make_registry_with("research"),
            a2a_queue=A2AQueue(),
        )
        token = set_services(services)
        try:
            state = _state(owl_name="phantom-owl")
            result = await dispatch.run(state)
            assert result.owl_name == "secretary"
        finally:
            reset_services(token)

    async def test_known_owl_passes_through(self) -> None:
        from stackowl.pipeline.services import reset_services, set_services

        services = StepServices(
            owl_registry=_make_registry_with("research"),
            a2a_queue=A2AQueue(),
        )
        token = set_services(services)
        try:
            state = _state(owl_name="research")
            result = await dispatch.run(state)
            assert result.owl_name == "research"
        finally:
            reset_services(token)


# ---------------------------------------------------------------------------
# A2ADelegator
# ---------------------------------------------------------------------------


class TestA2ADelegator:
    def test_init_rejects_non_positive_timeout(self) -> None:
        with pytest.raises(ValueError):
            A2ADelegator(a2a_queue=A2AQueue(), services=StepServices(), timeout_seconds=0)
        with pytest.raises(ValueError):
            A2ADelegator(a2a_queue=A2AQueue(), services=StepServices(), timeout_seconds=-1)

    async def test_delegate_round_trip_returns_response(self) -> None:
        queue = A2AQueue()
        provider_registry = _make_provider_registry("research", canned="research result")
        owl_registry = _make_registry_with("research")
        services = StepServices(
            provider_registry=provider_registry,
            owl_registry=owl_registry,
            a2a_queue=queue,
        )
        delegator = A2ADelegator(a2a_queue=queue, services=services, timeout_seconds=5.0)

        parent = _state(owl_name="secretary", trace_id="trace-rt")
        response = await delegator.delegate(
            from_owl="secretary",
            to_owl="research",
            sub_task="please research",
            parent_state=parent,
        )

        # MockProvider streams each whitespace-separated word + " "
        assert response.strip() == "research result"

    async def test_delegate_preserves_trace_id_in_messages(self) -> None:
        queue = A2AQueue()
        provider_registry = _make_provider_registry("research", canned="ok")
        owl_registry = _make_registry_with("research")
        services = StepServices(
            provider_registry=provider_registry,
            owl_registry=owl_registry,
            a2a_queue=queue,
        )
        delegator = A2ADelegator(a2a_queue=queue, services=services, timeout_seconds=5.0)

        # Sniff the specialist's mailbox before the sub-pipeline drains it.
        seen: list[A2AMessage] = []
        original_send = queue.send

        def _spy_send(msg: A2AMessage) -> None:
            seen.append(msg)
            original_send(msg)

        queue.send = _spy_send  # type: ignore[method-assign]

        parent = _state(owl_name="secretary", trace_id="trace-propagation")
        await delegator.delegate(
            from_owl="secretary",
            to_owl="research",
            sub_task="task body",
            parent_state=parent,
        )

        assert any(
            m.message_type == "request"
            and m.from_owl == "secretary"
            and m.to_owl == "research"
            and m.trace_id == "trace-propagation"
            for m in seen
        )
        assert any(
            m.message_type == "response"
            and m.from_owl == "research"
            and m.to_owl == "secretary"
            and m.trace_id == "trace-propagation"
            for m in seen
        )

    async def test_delegate_returns_empty_on_timeout(self) -> None:
        """If the specialist never replies, delegate returns '' and logs a warning."""

        class _BlockingProvider(MockProvider):
            async def stream(  # type: ignore[override]
                self, messages: Any, model: str, **kwargs: Any
            ) -> Any:
                # Block forever so the specialist never produces a response in time.
                await asyncio.sleep(10)
                if False:  # pragma: no cover — keeps this a valid AsyncIterator
                    yield ""

        queue = A2AQueue()
        provider_registry = ProviderRegistry()
        provider_registry.register_mock(
            "slowpoke",
            _BlockingProvider(name="slowpoke", canned_text="never sent"),
            tier="fast",
        )
        owl_registry = _make_registry_with("slowpoke")
        services = StepServices(
            provider_registry=provider_registry,
            owl_registry=owl_registry,
            a2a_queue=queue,
        )
        delegator = A2ADelegator(a2a_queue=queue, services=services, timeout_seconds=0.1)

        parent = _state(owl_name="secretary", trace_id="trace-timeout")
        response = await delegator.delegate(
            from_owl="secretary",
            to_owl="slowpoke",
            sub_task="anything",
            parent_state=parent,
        )

        assert response == ""

    async def test_timeout_seconds_property_exposed(self) -> None:
        delegator = A2ADelegator(
            a2a_queue=A2AQueue(),
            services=StepServices(),
            timeout_seconds=7.5,
        )
        assert delegator.timeout_seconds == 7.5
