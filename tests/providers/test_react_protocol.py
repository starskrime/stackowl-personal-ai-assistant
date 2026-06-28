"""Tests for the ReAct text-protocol tool fallback (Phase A1).

Covers the parser (`parse_react_action`), the text catalog renderer
(`ToolRegistry.render_text_catalog`), and the provider loop fallback that lets a
model without native tool_calls still dispatch tools via parseable text.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers._react import parse_react_action
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# Parser unit tests
# --------------------------------------------------------------------------- #


def test_parse_clean_action_with_fenced_json() -> None:
    text = 'ACTION: web_search\n```json\n{"query": "weather", "limit": 3}\n```'
    result = parse_react_action(text)
    assert result == ("web_search", {"query": "weather", "limit": 3})


def test_parse_action_with_bare_object() -> None:
    text = 'ACTION: read_file\n{"path": "/etc/hosts"}'
    result = parse_react_action(text)
    assert result == ("read_file", {"path": "/etc/hosts"})


def test_parse_action_no_args_is_empty_dict() -> None:
    text = "ACTION: list_tools\n"
    result = parse_react_action(text)
    assert result == ("list_tools", {})


def test_parse_flattened_newline_action_repairs_name() -> None:
    # The leak case: the model flattened its newline so the tool name runs into the
    # JSON ("skill_managen"). With the known set, the trailing char is repaired.
    text = 'ACTION: skill_managen{"action": "create"}'
    result = parse_react_action(text, known={"skill_manage", "web_search"})
    assert result == ("skill_manage", {"action": "create"})


def test_parse_unknown_name_rejected_when_known_given() -> None:
    text = 'ACTION: definitely_not_a_tool\n{"x": 1}'
    assert parse_react_action(text, known={"web_search"}) is None


def test_parse_without_known_trusts_name_backcompat() -> None:
    text = 'ACTION: web_search\n{"query": "x"}'
    assert parse_react_action(text) == ("web_search", {"query": "x"})


# --------------------------------------------------------------------------- #
# Bare-JSON tool call (native shape emitted as content) — symmetric with
# looks_like_tool_call so a capable model's call is dispatched, not bounced.
# --------------------------------------------------------------------------- #


def test_parse_bare_json_name_arguments() -> None:
    text = '{"name": "skill_manage", "arguments": {"action": "create"}}'
    assert parse_react_action(text, known={"skill_manage"}) == (
        "skill_manage",
        {"action": "create"},
    )


def test_parse_bare_json_alt_keys() -> None:
    # tool/args and action/action_input variants the model may emit.
    assert parse_react_action('{"tool": "web_search", "args": {"query": "x"}}') == (
        "web_search",
        {"query": "x"},
    )
    assert parse_react_action(
        '{"action": "read_file", "action_input": {"path": "/etc/hosts"}}'
    ) == ("read_file", {"path": "/etc/hosts"})


def test_parse_bare_json_no_args_is_empty_dict() -> None:
    assert parse_react_action('{"name": "list_tools"}') == ("list_tools", {})


def test_parse_bare_json_unknown_name_rejected() -> None:
    text = '{"name": "definitely_not_a_tool", "arguments": {}}'
    assert parse_react_action(text, known={"web_search"}) is None


def test_parse_bare_json_not_a_tool_call_returns_none() -> None:
    # An object-shaped value with no name key is not a tool call.
    assert parse_react_action('{"answer": "Paris", "confidence": 0.9}') is None


def test_looks_like_tool_call_detects_action_block() -> None:
    from stackowl.providers._react import looks_like_tool_call

    assert looks_like_tool_call('ACTION: skill_manage\n{"action": "create"}')


def test_looks_like_tool_call_detects_bare_json_object() -> None:
    from stackowl.providers._react import looks_like_tool_call

    assert looks_like_tool_call('{"action": "create", "name": "x"}')
    # object-shaped but unparseable (flattened) is still a leaked attempt
    assert looks_like_tool_call('{"action": "create" "name": "x"}')


def test_looks_like_tool_call_false_for_real_answer() -> None:
    from stackowl.providers._react import looks_like_tool_call

    assert not looks_like_tool_call("The capital of France is Paris.")
    assert not looks_like_tool_call("")
    assert not looks_like_tool_call(None)


def test_parse_malformed_json_returns_none() -> None:
    text = "ACTION: web_search\n```json\n{not valid json}\n```"
    assert parse_react_action(text) is None


def test_parse_no_action_returns_none() -> None:
    assert parse_react_action("Here is my final answer, no tools needed.") is None


def test_parse_none_input_returns_none() -> None:
    assert parse_react_action(None) is None


def test_parse_action_non_dict_json_returns_none() -> None:
    text = "ACTION: web_search\n```json\n[1, 2, 3]\n```"
    assert parse_react_action(text) is None


def test_parse_action_with_markdown_prefix() -> None:
    # Models often emit list/quote markdown prefixes; structural tokens must survive.
    text = '- ACTION: web_search\n```json\n{"query": "x"}\n```'
    assert parse_react_action(text) == ("web_search", {"query": "x"})


def test_parse_action_with_blockquote_prefix() -> None:
    text = '> ACTION: web_search\n```json\n{"query": "x"}\n```'
    assert parse_react_action(text) == ("web_search", {"query": "x"})


def test_parse_action_surrounded_by_prose() -> None:
    text = (
        "I should look this up.\n"
        "ACTION: web_search\n"
        '```json\n{"query": "tokyo"}\n```\n'
        "Then I will summarize."
    )
    assert parse_react_action(text) == ("web_search", {"query": "tokyo"})


# --------------------------------------------------------------------------- #
# render_text_catalog
# --------------------------------------------------------------------------- #


def test_render_text_catalog_includes_names_and_args() -> None:
    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from disk.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
    ]
    catalog = ToolRegistry().render_text_catalog(schemas)
    assert "web_search" in catalog
    assert "query" in catalog
    assert "limit" in catalog
    assert "read_file" in catalog
    assert "path" in catalog
    assert "ACTION:" in catalog


def test_render_text_catalog_skips_malformed_entry() -> None:
    schemas: list[dict[str, Any]] = [
        {"type": "function"},  # missing "function" body
        {"function": {"name": "ok_tool", "parameters": {"properties": {"a": {}}}}},
        "not even a dict",  # type: ignore[list-item]
    ]
    # Must not raise; must still render the good one.
    catalog = ToolRegistry().render_text_catalog(schemas)
    assert "ok_tool" in catalog
    assert "a" in catalog


# --------------------------------------------------------------------------- #
# Provider loop fallback (red -> green)
# --------------------------------------------------------------------------- #


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self._i = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        # Record a shallow copy of messages so later mutation doesn't corrupt history.
        self.calls.append([dict(m) for m in kwargs["messages"]])
        resp = self._responses[self._i]
        self._i += 1
        return resp


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.chat = _FakeChat(_FakeCompletions(responses))


def _make_provider(
    client: _FakeClient, *, supports_native_tools: bool = True
) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="local",
        supports_native_tools=supports_native_tools,
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_react_fallback_dispatches_tool_and_returns_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Call 1: no native tool_calls, but an ACTION block in text.
    react_msg = _FakeMessage(
        content='ACTION: web_search\n```json\n{"query": "tokyo weather"}\n```',
        tool_calls=None,
    )
    # Call 2: plain final answer.
    final_msg = _FakeMessage(content="It is sunny in Tokyo.", tool_calls=None)
    client = _FakeClient([_FakeResponse(react_msg), _FakeResponse(final_msg)])
    # Legacy endpoint WITHOUT native tool-calls — the text catalog IS injected here.
    provider = _make_provider(client, supports_native_tools=False)

    dispatched: list[tuple[str, dict[str, Any]]] = []

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        dispatched.append((name, args))
        return "RESULT: sunny, 22C"

    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    text, calls = await provider.complete_with_tools(
        user_text="what is the weather in tokyo?",
        system_text="You are a helpful owl.",
        tool_schemas=schemas,
        tool_dispatcher=dispatcher,
    )

    # Dispatcher invoked with the parsed action.
    assert dispatched == [("web_search", {"query": "tokyo weather"})]
    # Final answer returned.
    assert text == "It is sunny in Tokyo."
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"
    assert calls[0]["result"] == "RESULT: sunny, 22C"

    # An OBSERVATION turn was fed back to the model on the second call.
    second_call_messages = client.chat.completions.calls[1]
    assert any(
        m.get("role") == "user" and "OBSERVATION" in str(m.get("content", ""))
        for m in second_call_messages
    )
    # The text catalog was appended to the system message.
    first_call_messages = client.chat.completions.calls[0]
    system_msgs = [m for m in first_call_messages if m.get("role") == "system"]
    assert system_msgs
    assert "ACTION:" in str(system_msgs[0]["content"])
    assert "web_search" in str(system_msgs[0]["content"])


@pytest.mark.asyncio
async def test_native_default_skips_catalog_and_dispatches_native_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-cause regression: with supports_native_tools=True (the default), the text
    ACTION catalog is NOT injected (no interference) and a native tool_call dispatches."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    # Call 1: native tool_call (the shape capable Ollama models actually emit).
    native_msg = _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall("call_1", "web_search", '{"query": "tokyo"}')],
    )
    # Call 2: plain final answer.
    final_msg = _FakeMessage(content="It is sunny in Tokyo.", tool_calls=None)
    client = _FakeClient([_FakeResponse(native_msg), _FakeResponse(final_msg)])
    provider = _make_provider(client)  # default: supports_native_tools=True

    dispatched: list[tuple[str, dict[str, Any]]] = []

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        dispatched.append((name, args))
        return "RESULT: sunny, 22C"

    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]

    text, calls = await provider.complete_with_tools(
        user_text="what is the weather in tokyo?",
        system_text="You are a helpful owl.",
        tool_schemas=schemas,
        tool_dispatcher=dispatcher,
    )

    assert dispatched == [("web_search", {"query": "tokyo"})]
    assert text == "It is sunny in Tokyo."
    # The text catalog was NOT appended — system message is the clean prompt only.
    first_call_messages = client.chat.completions.calls[0]
    system_msgs = [m for m in first_call_messages if m.get("role") == "system"]
    assert system_msgs
    assert "ACTION:" not in str(system_msgs[0]["content"])
    assert str(system_msgs[0]["content"]) == "You are a helpful owl."


# --------------------------------------------------------------------------- #
# Failure marker is an INTERNAL signal — stripped before model/telemetry, and
# carried as the typed ``failed`` flag (refinement of H3).
# --------------------------------------------------------------------------- #


def _web_search_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web.",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_react_failed_dispatch_strips_marker_and_flags_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED dispatch (marker prefix) on the ReAct path must: feed the model a
    marker-free OBSERVATION, store a marker-free result, and record failed=True."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    react_msg = _FakeMessage(
        content='ACTION: web_search\n```json\n{"query": "x"}\n```',
        tool_calls=None,
    )
    final_msg = _FakeMessage(content="done", tool_calls=None)
    client = _FakeClient([_FakeResponse(react_msg), _FakeResponse(final_msg)])
    provider = _make_provider(client)

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return f"{TOOL_FAILED_MARKER}web_search: network unreachable"

    text, calls = await provider.complete_with_tools(
        user_text="q",
        system_text="sys",
        tool_schemas=_web_search_schema(),
        tool_dispatcher=dispatcher,
    )

    # all_calls entry: clean result, explicit failed=True.
    assert len(calls) == 1
    assert calls[0]["failed"] is True
    assert TOOL_FAILED_MARKER not in str(calls[0]["result"])
    assert "\x00" not in str(calls[0]["result"])
    assert "network unreachable" in str(calls[0]["result"])

    # The OBSERVATION fed back to the model on call 2 carries NO marker / NUL.
    second_call_messages = client.chat.completions.calls[1]
    obs = [
        str(m.get("content", ""))
        for m in second_call_messages
        if m.get("role") == "user" and "OBSERVATION" in str(m.get("content", ""))
    ]
    assert obs
    assert all(TOOL_FAILED_MARKER not in o and "\x00" not in o for o in obs)


@pytest.mark.asyncio
async def test_react_successful_dispatch_has_failed_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful dispatch records failed=False and a clean (no-op strip) result."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    react_msg = _FakeMessage(
        content='ACTION: web_search\n```json\n{"query": "x"}\n```',
        tool_calls=None,
    )
    final_msg = _FakeMessage(content="done", tool_calls=None)
    client = _FakeClient([_FakeResponse(react_msg), _FakeResponse(final_msg)])
    provider = _make_provider(client)

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return "RESULT: ok"

    _text, calls = await provider.complete_with_tools(
        user_text="q",
        system_text="sys",
        tool_schemas=_web_search_schema(),
        tool_dispatcher=dispatcher,
    )

    assert len(calls) == 1
    assert calls[0]["failed"] is False
    assert calls[0]["result"] == "RESULT: ok"
    assert TOOL_FAILED_MARKER not in str(calls[0]["result"])


@pytest.mark.asyncio
async def test_native_failed_dispatch_strips_marker_and_flags_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guarantee on the NATIVE tool_calls path: the ``tool`` message content
    sent to the API is marker-free and the call records failed=True."""
    from stackowl.pipeline.persistence import TOOL_FAILED_MARKER

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    tool_call = _FakeToolCall("call_1", "web_search", '{"query": "x"}')
    tool_msg = _FakeMessage(content=None, tool_calls=[tool_call])
    final_msg = _FakeMessage(content="done", tool_calls=None)
    client = _FakeClient([_FakeResponse(tool_msg), _FakeResponse(final_msg)])
    provider = _make_provider(client)

    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return f"{TOOL_FAILED_MARKER}web_search: blocked"

    _text, calls = await provider.complete_with_tools(
        user_text="q",
        system_text="sys",
        tool_schemas=_web_search_schema(),
        tool_dispatcher=dispatcher,
    )

    assert len(calls) == 1
    assert calls[0]["failed"] is True
    assert TOOL_FAILED_MARKER not in str(calls[0]["result"])

    # The tool message content sent on call 2 carries no marker.
    second_call_messages = client.chat.completions.calls[1]
    tool_msgs = [
        str(m.get("content", ""))
        for m in second_call_messages
        if m.get("role") == "tool"
    ]
    assert tool_msgs
    assert all(TOOL_FAILED_MARKER not in t and "\x00" not in t for t in tool_msgs)


# --------------------------------------------------------------------------- #
# Leak guard: an unparsed tool call must NEVER be delivered as the final answer
# --------------------------------------------------------------------------- #


async def _noop_dispatch(name: str, args: dict[str, Any]) -> str:
    return ""


@pytest.mark.asyncio
async def test_leak_guard_escalates_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Bare JSON object as the "final answer" — a leaked tool-call attempt.
    leak = '{"action": "create", "name": "x"}'
    responses = [_FakeResponse(_FakeMessage(content=leak)) for _ in range(4)]
    provider = _make_provider(_FakeClient(responses))
    text, _calls = await provider.complete_with_tools(
        user_text="make a skill", system_text=None, tool_schemas=[],
        tool_dispatcher=_noop_dispatch, can_escalate=True,
    )
    assert text == "ESCALATE"  # never the raw JSON; gateway will step up a tier


@pytest.mark.asyncio
async def test_leak_guard_floors_when_no_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    leak = '{"action": "create", "name": "x"}'
    responses = [_FakeResponse(_FakeMessage(content=leak)) for _ in range(4)]
    provider = _make_provider(_FakeClient(responses))
    text, _calls = await provider.complete_with_tools(
        user_text="make a skill", system_text=None, tool_schemas=[],
        tool_dispatcher=_noop_dispatch, can_escalate=False,
    )
    assert text != leak
    assert '{"action"' not in text  # raw tool call never leaks
    assert text  # honest floor is non-empty
