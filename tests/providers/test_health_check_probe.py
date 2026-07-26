"""DEBT-6 — the default provider health probe must be able to succeed.

Live break (2026-07-26): ``ModelProvider.health_check()`` sent ``"ping"`` with
``max_tokens=1`` and NO ``disable_thinking``. The deployed model emits its
reasoning into a separate ``reasoning_content`` field and needs ~150 completion
tokens before it emits its first *content* token, so ``content`` came back empty
EVERY time. Measured against the live gateway that day: max_tokens 1/16/64 all
returned ``finish_reason="length"`` with empty content; 256 was the first budget
that produced any. With ``enable_thinking: False`` the same 1-token budget
returns ``"pong"`` and zero reasoning.

The consequence was 193 probe events in one day, each burning TWO paid calls
(the probe plus ``complete()``'s empty-retry backstop) and yielding nothing —
while ``health_check()`` still reported ``ok``, because it only degraded on an
exception.

These tests drive the two guarantees:
  1. The probe asks for a reply the model can actually give (thinking disabled,
     a budget above one token).
  2. An empty completion is reported ``degraded`` with a reason, not ``ok``.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.openai_provider import OpenAIProvider


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]
        self.model = "acme-v1"
        self.usage = None


class _ScriptedCompletions:
    def __init__(self, contents: list[str | None]) -> None:
        self._contents = contents
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


class _FakeChat:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="acme",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="acme-v1",
        tier="fast",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_health_probe_disables_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must tell a reasoning endpoint to skip its reasoning block.

    Without this the entire (deliberately tiny) probe budget is spent on
    reasoning the probe immediately discards, and content is always empty.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["pong"])
    provider = _make_provider(_FakeClient(completions))

    await provider.health_check()

    extra_body = completions.calls[0].get("extra_body", {})
    assert extra_body.get("chat_template_kwargs") == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_health_probe_asks_for_more_than_one_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_tokens=1 truncates even a one-word acknowledgement (measured:
    finish_reason="length"). The probe needs a budget that can produce a
    complete short reply, so a clean probe is distinguishable from a truncated
    one — while staying far too small to be a shaping cap on real output."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["pong"])
    provider = _make_provider(_FakeClient(completions))

    await provider.health_check()

    assert completions.calls[0]["max_tokens"] > 1


@pytest.mark.asyncio
async def test_health_probe_makes_exactly_one_call_when_the_model_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy probe costs ONE paid call. The 2-calls-per-probe burn was half
    the cost of this defect."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["pong"])
    provider = _make_provider(_FakeClient(completions))

    status = await provider.health_check()

    assert status.status == "ok"
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_health_probe_degraded_when_completion_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bakir, 2026-07-26: a probe that got nothing back reports degraded with
    the reason. Reporting ok on an empty generation left a provider that
    produces nothing looking green for a full day."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["", ""])  # empty, and empty again on retry
    provider = _make_provider(_FakeClient(completions))

    status = await provider.health_check()

    assert status.status == "degraded"
    assert status.message is not None
    assert "empty" in status.message.lower()


@pytest.mark.asyncio
async def test_health_probe_degraded_when_completion_is_only_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace is not an answer — the same verdict as empty."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["   \n  ", "   \n  "])
    provider = _make_provider(_FakeClient(completions))

    status = await provider.health_check()

    assert status.status == "degraded"


@pytest.mark.asyncio
async def test_health_probe_degraded_on_provider_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-existing behaviour, pinned so the empty-content change does not
    quietly replace it: a raising provider is still degraded, with the error."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    class _BoomCompletions:
        calls: list[dict[str, Any]] = []  # noqa: RUF012

        async def create(self, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError("connection refused")

    provider = _make_provider(_FakeClient(_BoomCompletions()))  # type: ignore[arg-type]

    status = await provider.health_check()

    assert status.status == "degraded"
    assert status.message is not None
