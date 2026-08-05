"""D02.6 — the COMPRESS actuator, WIRED into the real OpenAI tool loop.

The unit tests next door prove the base-class loop. This proves the provider
actually hands it a working shrink closure — the D05.2 lesson, where 19 module
tests passed while the module was never called.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.openai_provider import OpenAIProvider


class _TooLarge(Exception):
    status_code = 413


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, msg: _Msg) -> None:
        self.choices = [_Choice(msg)]
        self.model = "m"
        self.usage = None


class _ToolThenReject:
    """Emits tool calls for ``tool_rounds`` rounds (each producing a large
    observation), then rejects while the accumulated history exceeds ``limit``.

    This is the shape COMPRESS actually serves: a LONG tool loop whose old
    observations are elidable. A short history cannot be compressed at all — the
    trimmer protects the system message, the first user message and the two most
    recent, which on a 4-message history is all of them.
    """

    def __init__(self, limit: int, tool_rounds: int = 3) -> None:
        self.limit = limit
        self.tool_rounds = tool_rounds
        self.sizes: list[int] = []
        self.n = 0

    async def create(self, **kwargs: Any) -> _Resp:
        self.n += 1
        size = sum(len(str(m.get("content") or "")) for m in kwargs["messages"])
        self.sizes.append(size)
        if self.n <= self.tool_rounds:
            return _Resp(_Msg(None, [_ToolCall(f"c{self.n}", "fetch", "{}")]))
        if size > self.limit:
            raise _TooLarge()
        return _Resp(_Msg("done"))


class _Chat:
    def __init__(self, c: _ToolThenReject) -> None:
        self.completions = c


class _Client:
    def __init__(self, c: _ToolThenReject) -> None:
        self.chat = _Chat(c)


def _provider(client: _Client, context_chars: int) -> OpenAIProvider:
    cfg = ProviderConfig(
        name="local", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="m", tier="fast", context_chars=context_chars,
    )
    p = OpenAIProvider(cfg, api_key="")
    p._client = client  # type: ignore[assignment]
    return p


_SCHEMAS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "fetch", "description": "Fetch a large document.",
        "parameters": {"type": "object", "properties": {}}}}
]


async def _dispatch(name: str, args: dict[str, Any]) -> str:
    """A tool result far larger than the endpoint will accept."""
    return "o" * 400_000


@pytest.mark.asyncio
async def test_an_oversize_rejection_is_compressed_and_the_turn_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolThenReject(limit=250_000)
    provider = _provider(_Client(completions), context_chars=4_000_000)

    text, _calls = await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatch, max_iterations=8,
    )

    assert text == "done", f"the turn must survive an oversize rejection: {completions.sizes}"
    # round 1 (small) -> tool -> round 2 (oversize, rejected) -> compressed retry
    assert len(completions.sizes) >= 3, completions.sizes
    assert completions.sizes[-1] < completions.sizes[-2], (
        f"the retry must be SMALLER, got {completions.sizes}"
    )


@pytest.mark.asyncio
async def test_the_provider_learns_the_limit_rather_than_rediscovering_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healing, not just recovering: after one rejection the provider must carry
    a lowered ceiling, so the next turn does not pay the same toll."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolThenReject(limit=250_000)
    provider = _provider(_Client(completions), context_chars=4_000_000)

    await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatch, max_iterations=8,
    )

    assert provider._learned_context_chars is not None, (
        "a rejection that taught us the real limit must be remembered"
    )
    assert provider._effective_context_budget(4_000_000) < 4_000_000


@pytest.mark.asyncio
async def test_an_uncompressible_payload_surfaces_instead_of_pretending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE HONEST LIMIT, pinned deliberately. trim_messages_to_budget elides tool
    OBSERVATIONS only — it never touches the system prompt or a user message. So
    a single enormous user message cannot be compressed, and the actuator must
    say so rather than retry an identical request and call it a recovery.

    This is the boundary of the self-healing claim, and it belongs in a test
    rather than only in a docstring."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolThenReject(limit=1_000, tool_rounds=0)
    provider = _provider(_Client(completions), context_chars=4_000_000)

    with pytest.raises(Exception):
        await provider.complete_with_tools(
            user_text="x" * 400_000, system_text="sys", tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatch, max_iterations=8,
        )
    assert provider._learned_context_chars is None, (
        "nothing was actually compressed, so nothing was learned"
    )
