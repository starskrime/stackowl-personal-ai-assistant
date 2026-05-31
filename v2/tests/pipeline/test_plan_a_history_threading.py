import logging

import pytest
from stackowl.pipeline.steps.classify import _parse_turns_to_messages


def test_parse_turns_splits_user_and_assistant():
    rows = ["User: hello\n\nAssistant: hi there",
            "User: find aws practice\n\nAssistant: here are some"]
    msgs = _parse_turns_to_messages(rows)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi there"


def test_parse_turns_tolerates_missing_assistant():
    msgs = _parse_turns_to_messages(["User: just a question"])
    assert msgs[0].role == "user" and msgs[0].content == "just a question"
    assert all(m.content for m in msgs)  # never emits empty-content turns


# ---------------------------------------------------------------------------
# RC-B / RC-C wiring: execute must forward state.system_prompt + state.history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_tool_loop_passes_history_and_system_prompt(monkeypatch):
    """Tool-loop path: complete_with_tools must receive system_prompt and history."""
    from stackowl.pipeline.services import StepServices, set_services, reset_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute
    from stackowl.providers.base import Message

    captured: dict = {}

    class _FakeProvider:
        protocol = "anthropic"

        async def complete_with_tools(
            self, user_text, system_text, tool_schemas, tool_dispatcher,
            max_iterations=8, history=None,
        ):
            captured["system_text"] = system_text
            captured["history"] = history
            return "fake-response", []

    class _FakeProviderRegistry:
        def get(self, name):
            return _FakeProvider()

        def get_by_tier(self, tier):
            return _FakeProvider()

    class _FakeToolRegistry:
        """Non-empty registry so the tool-loop branch is taken."""
        def all(self):
            return [object()]  # truthy — triggers tool-loop branch

        def to_provider_schema(self, protocol, *, profile=None, pins=None, hydrated=None):
            return []

        def get(self, name):
            return None

    services = StepServices(
        provider_registry=_FakeProviderRegistry(),
        tool_registry=_FakeToolRegistry(),
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id="t1", session_id="s1", input_text="now",
            channel="cli", owl_name="secretary", pipeline_step="execute",
            system_prompt="SYS-PERSONA",
            history=(Message(role="user", content="earlier turn"),),
        )
        await execute.run(state)
    finally:
        reset_services(token)

    assert captured.get("system_text") == "SYS-PERSONA", (
        f"expected system_text='SYS-PERSONA', got {captured.get('system_text')!r}"
    )
    history_received = captured.get("history") or []
    assert [m.content for m in history_received] == ["earlier turn"], (
        f"expected history=['earlier turn'], got {[m.content for m in history_received]}"
    )


@pytest.mark.asyncio
async def test_execute_streaming_branch_builds_history_messages(monkeypatch):
    """No-tool streaming branch: message list must be [system, *history, user]."""
    from stackowl.pipeline.services import StepServices, set_services, reset_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import execute
    from stackowl.providers.base import Message

    captured_messages: list = []

    async def _fake_stream(messages, model=""):
        captured_messages.extend(messages)
        return
        yield  # make it an async generator

    class _FakeProvider:
        protocol = "anthropic"

        def stream(self, messages, model=""):
            captured_messages.extend(messages)
            async def _gen():
                return
                yield
            return _gen()

    class _FakeProviderRegistry:
        def get(self, name):
            return _FakeProvider()

        def get_by_tier(self, tier):
            return _FakeProvider()

    # tool_registry=None → streaming branch
    services = StepServices(
        provider_registry=_FakeProviderRegistry(),
        tool_registry=None,
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id="t2", session_id="s2", input_text="current",
            channel="cli", owl_name="secretary", pipeline_step="execute",
            system_prompt="SYS-PERSONA",
            history=(Message(role="assistant", content="prior response"),),
        )
        await execute.run(state)
    finally:
        reset_services(token)

    # Expected order: [system, prior-assistant, current-user]
    assert len(captured_messages) == 3, (
        f"expected 3 messages, got {len(captured_messages)}: "
        f"{[m.role for m in captured_messages]}"
    )
    assert captured_messages[0].role == "system"
    assert captured_messages[0].content == "SYS-PERSONA"
    assert captured_messages[1].role == "assistant"
    assert captured_messages[1].content == "prior response"
    assert captured_messages[2].role == "user"
    assert captured_messages[2].content == "current"


# ---------------------------------------------------------------------------
# H2 — no-hidden-errors: _gather_history fetch failure must log at ERROR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gather_history_logs_error_on_fetch_failure(caplog):
    """When recent_conversation_turns raises, _gather_history must return []
    (self-heal) AND log at ERROR level (not just warning)."""
    from stackowl.pipeline.services import StepServices, set_services, reset_services
    from stackowl.pipeline.steps.classify import _gather_history

    class _BrokenBridge:
        async def recent_conversation_turns(self, session_id, limit):
            raise RuntimeError("simulated bridge failure")

    token = set_services(StepServices(memory_bridge=_BrokenBridge()))
    try:
        with caplog.at_level(logging.ERROR, logger="stackowl.engine"):
            result = await _gather_history("session-x", limit=6)
    finally:
        reset_services(token)

    # Self-heals to empty list
    assert result == []
    # ERROR was logged (not just a warning)
    assert any(
        r.levelno == logging.ERROR and "history fetch FAILED" in r.getMessage()
        for r in caplog.records
    ), f"Expected ERROR log not found. Records: {[r.getMessage() for r in caplog.records]}"
