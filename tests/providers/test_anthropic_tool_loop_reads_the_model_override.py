"""The Anthropic tool loop must honour `models[].max_output_tokens`, like its siblings.

THE DEFECT, and what makes it worse than an ordinary inconsistency. `ProviderConfig`
lets an operator set a per-model output cap, and `resolve_model_override()` is the one
function that reads it. `AnthropicProvider.stream()` uses it, and
`AnthropicProvider.complete()` uses it. `AnthropicProvider.complete_with_tools()` — the
agentic loop, the path that matters most — read `self._config.max_output_tokens` RAW, at
both of its round-builders.

So the same fact was read two different ways inside one class: this repo's third
recurring defect shape.

AND THE DOCUMENTED REMEDY POINTED AT THE PATH THAT IGNORED IT. The comment above both
sibling call sites says:

    max_output_tokens' 250000 default exceeds real Anthropic ceilings ... the FIRST
    Anthropic provider added must set an explicit models[].max_output_tokens
    (or a smaller provider-level max_output_tokens)

An operator who followed that instruction to the letter would still have had their first
agentic turn rejected, because the tool loop never consulted the override they had just
been told to set. Real Anthropic ceilings are 8192-64000 against a 250000 default.

ONE THING THE COMMENT CANNOT MEAN, found by writing these tests: `_validate_models`
(`config/provider.py:274`) REJECTS a `models[]` entry whose name equals `default_model`.
So the `models[].max_output_tokens` half of that remedy is only reachable for a
NON-default model; capping the default one requires the parenthetical, a smaller
provider-level `max_output_tokens`. These tests therefore exercise the reachable case —
a tier-routed model that is not the provider default.

SCOPE, STATED HONESTLY: latent. No Anthropic backend is configured on this deployment —
every entry in stackowl.yaml is `protocol: openai` — so this has never fired in
production and cannot until one is added. It is fixed now because it is a one-line
inconsistency found while building D04.1's divergence inventory, and because the whole
point of that item is what happens when a second backend IS added.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider

pytestmark = pytest.mark.asyncio

_SCHEMAS = [{"name": "web_search", "description": "d", "input_schema": {"type": "object"}}]


class _Block:
    def __init__(self, type: str, text: str = "") -> None:
        self.type, self.text = type, text


class _Response:
    def __init__(self) -> None:
        self.stop_reason, self.content, self.usage = "end_turn", [_Block("text", "done")], None


class _Messages:
    """Records the max_tokens of every request the loop issues."""

    def __init__(self) -> None:
        self.seen: list[int] = []
        self.n = 0

    async def count_tokens(self, **_kw: Any) -> Any:
        class _C:
            input_tokens = 10

        return _C()

    async def create(self, **kwargs: Any) -> _Response:
        self.n += 1
        self.seen.append(int(kwargs.get("max_tokens", -1)))
        return _Response()


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _provider(messages: _Messages, **cfg: Any) -> AnthropicProvider:
    base: dict[str, Any] = {
        "name": "claude", "protocol": "anthropic",
        "default_model": "claude-opus", "tiers": ("powerful",),
    }
    base.update(cfg)
    provider = AnthropicProvider(ProviderConfig(**base), api_key="x")
    provider._client = _Client(messages)  # noqa: SLF001
    return provider


async def _dispatch(_name: str, _args: dict[str, Any]) -> str:
    return "ok"


@pytest.fixture(autouse=True)
def _not_test_mode(monkeypatch):
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


class TestTheToolLoopHonoursThePerModelCap:
    async def test_it_uses_the_models_entry_not_the_provider_default(self) -> None:
        """THE CASE THE COMMENT TELLS THE OPERATOR TO SET UP."""
        messages = _Messages()
        provider = _provider(
            messages,
            max_output_tokens=250_000,
            models=({"name": "claude-sonnet", "tiers": ("powerful",),
                     "max_output_tokens": 8_192},),
        )

        await provider.complete_with_tools(
            user_text="hi", system_text="sys", tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatch, model="claude-sonnet", max_iterations=2,
        )

        assert messages.seen, "the loop issued no request"
        assert all(m == 8_192 for m in messages.seen), messages.seen

    async def test_an_explicit_per_call_max_tokens_still_wins(self) -> None:
        """The 2026-07-22 incident parameter must keep outranking config."""
        messages = _Messages()
        provider = _provider(
            messages, max_output_tokens=250_000,
            models=({"name": "claude-sonnet", "tiers": ("powerful",),
                     "max_output_tokens": 8_192},),
        )

        await provider.complete_with_tools(
            user_text="hi", system_text="sys", tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatch, model="claude-sonnet", max_iterations=2,
            max_tokens=1_234,
        )

        assert all(m == 1_234 for m in messages.seen), messages.seen

    async def test_no_override_falls_back_to_the_provider_value(self) -> None:
        """Unchanged behaviour for every config that sets no per-model cap — which is
        every config today."""
        messages = _Messages()
        provider = _provider(messages, max_output_tokens=64_000)

        await provider.complete_with_tools(
            user_text="hi", system_text="sys", tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatch, model="claude-sonnet", max_iterations=2,
        )

        assert all(m == 64_000 for m in messages.seen), messages.seen

    async def test_it_agrees_with_complete_for_the_same_model(self) -> None:
        """The real invariant: ONE fact, read ONE way. `complete()` already resolves
        the override; the loop must not answer differently for the same model, which
        is what made this defect shape #3 rather than a typo."""
        loop_msgs = _Messages()
        cfg: dict[str, Any] = {
            "max_output_tokens": 250_000,
            "models": ({"name": "claude-sonnet", "tiers": ("powerful",),
                        "max_output_tokens": 16_000},),
        }

        await _provider(loop_msgs, **cfg).complete_with_tools(
            user_text="hi", system_text="sys", tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatch, model="claude-sonnet", max_iterations=2,
        )
        from stackowl.providers.model_config import resolve_model_override

        expected = resolve_model_override(
            ProviderConfig(name="claude", protocol="anthropic",
                           default_model="claude-opus", tiers=("powerful",), **cfg),
            "claude-sonnet",
        )[0]

        assert loop_msgs.seen and all(m == expected for m in loop_msgs.seen), (
            loop_msgs.seen, expected
        )
