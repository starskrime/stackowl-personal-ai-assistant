"""F-21 — ``AnthropicProvider`` must wrap a raw transport fault as ProviderError.

Previously ``stream()`` / ``complete()`` caught ONLY ``anthropic.APIError``; a raw
``ConnectionError`` / ``TimeoutError`` (not an SDK error type) escaped as a
non-ProviderError, breaking the gateway's uniform fault classification. These
tests lock the broader transport-set wrap — while a routing control signal
(``CircuitOpenError``) still propagates UNWRAPPED so the cascade can classify it.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.exceptions import CircuitOpenError, ProviderError
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.base import Message

pytestmark = pytest.mark.asyncio


class _RaisingMessages:
    """messages.create raises a configured exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def create(self, **kwargs: Any) -> Any:
        raise self._exc


class _RaisingStreamCtx:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> Any:
        raise self._exc

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _StreamMessages:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def stream(self, **kwargs: Any) -> _RaisingStreamCtx:
        return _RaisingStreamCtx(self._exc)


class _FakeClient:
    def __init__(self, messages: Any) -> None:
        self.messages = messages


def _make_provider(client: _FakeClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="anthropic", protocol="anthropic", default_model="claude-test", tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="k")
    provider._client = client  # type: ignore[assignment]
    return provider


async def test_complete_wraps_raw_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_FakeClient(_RaisingMessages(ConnectionError("dropped"))))
    with pytest.raises(ProviderError):
        await provider.complete([Message(role="user", content="hi")], model="")


async def test_complete_propagates_circuit_open_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A routing control signal must NOT be re-wrapped as a generic ProviderError —
    # the gateway needs to see CircuitOpenError to cascade to a higher tier.
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_FakeClient(_RaisingMessages(CircuitOpenError("anthropic", 5.0))))
    with pytest.raises(CircuitOpenError):
        await provider.complete([Message(role="user", content="hi")], model="")


async def test_stream_wraps_raw_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_FakeClient(_StreamMessages(TimeoutError("hung"))))
    with pytest.raises(ProviderError):
        async for _ in provider.stream([Message(role="user", content="hi")], model=""):
            pass


async def test_stream_propagates_circuit_open_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    provider = _make_provider(_FakeClient(_StreamMessages(CircuitOpenError("anthropic", 5.0))))
    with pytest.raises(CircuitOpenError):
        async for _ in provider.stream([Message(role="user", content="hi")], model=""):
            pass
