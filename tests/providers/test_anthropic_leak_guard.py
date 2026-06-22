"""Anthropic parity for the weak-model tool-call LEAK guard (mirrors openai_provider).

A model on the Anthropic protocol that emits a tool call as TEXT (an unparsed
``{"action": ...}`` / ``ACTION:`` block) instead of a native ``tool_use`` block
must NEVER have that raw text delivered. Below the ceiling the loop returns the
ESCALATE sentinel (the gateway re-runs on a stronger tier); at the ceiling it
returns an honest floor — never the raw leak.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.llm_gateway import ESCALATE_SENTINEL

_LEAK = '{"action": "create", "name": "x", "content": "---nname: y"}'
_SCHEMAS = [{"name": "skill_manage", "description": "d", "input_schema": {"type": "object"}}]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return "ok"


class _ABlock:
    def __init__(self, type: str, text: str = "") -> None:
        self.type = type
        self.text = text


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _AnthropicMessages:
    """Always returns an end_turn (non-tool_use) whose text is a leaked tool call."""

    def __init__(self, leak: str) -> None:
        self._leak = leak
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_count += 1
        return _AResponse("end_turn", [_ABlock("text", text=self._leak)])


class _AnthropicClient:
    def __init__(self, messages: _AnthropicMessages) -> None:
        self.messages = messages


def _make_provider(client: _AnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude", protocol="anthropic", default_model="claude-sonnet", tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_anthropic_persistent_leak_escalates_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_AnthropicClient(_AnthropicMessages(_LEAK)))

    text, _calls = await provider.complete_with_tools(
        user_text="create a skill", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, max_iterations=5, can_escalate=True,
    )

    assert text == ESCALATE_SENTINEL


@pytest.mark.asyncio
async def test_anthropic_persistent_leak_floors_when_no_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_AnthropicClient(_AnthropicMessages(_LEAK)))

    text, _calls = await provider.complete_with_tools(
        user_text="create a skill", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, max_iterations=5,  # can_escalate defaults False
    )

    # Never the raw leak, never silence — an honest floor owns the turn.
    assert text.strip()
    assert '"action"' not in text
    assert text != ESCALATE_SENTINEL
