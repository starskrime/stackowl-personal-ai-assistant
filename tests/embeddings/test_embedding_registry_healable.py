"""EmbeddingRegistry HealableResource conformance (ADR-6 self-heal, Task 1).

Verifies ``ensure_available()`` retries ``SentenceTransformerProvider.create()``
when the registry is degraded to the hash fallback, swapping back to semantic
on success and propagating on failure — closing the gap where a
network-unavailable boot degraded to hash embeddings permanently with no
later retry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from stackowl.embeddings.hash_provider import HashEmbeddingProvider
from stackowl.embeddings.registry import EmbeddingRegistry

pytestmark = pytest.mark.asyncio


def _degraded_registry() -> EmbeddingRegistry:
    """A registry in the hash-fallback state, as if semantic load failed at boot."""
    registry = EmbeddingRegistry()
    registry._provider = HashEmbeddingProvider()
    registry._is_semantic = False
    registry._model_name = "all-MiniLM-L6-v2"
    return registry


class _FakeSemanticProvider:
    """Minimal stand-in for a loaded SentenceTransformerProvider."""

    model_name = "all-MiniLM-L6-v2"
    dimension = 384


async def test_ensure_available_retries_and_flips_to_semantic_on_success() -> None:
    registry = _degraded_registry()
    fake_provider = _FakeSemanticProvider()

    with patch(
        "stackowl.embeddings.sentence_transformer_provider.SentenceTransformerProvider.create",
        new=AsyncMock(return_value=fake_provider),
    ) as mock_create:
        await registry.ensure_available()

    mock_create.assert_awaited_once_with("all-MiniLM-L6-v2")
    assert registry._is_semantic is True
    assert registry._provider is fake_provider


async def test_ensure_available_is_noop_when_already_semantic() -> None:
    registry = EmbeddingRegistry()
    registry._provider = _FakeSemanticProvider()  # type: ignore[assignment]
    registry._is_semantic = True

    with patch(
        "stackowl.embeddings.sentence_transformer_provider.SentenceTransformerProvider.create",
        new=AsyncMock(),
    ) as mock_create:
        await registry.ensure_available()

    mock_create.assert_not_awaited()
    assert registry._is_semantic is True
    assert registry._provider is not None


async def test_ensure_available_raises_and_stays_degraded_on_repeated_failure() -> None:
    registry = _degraded_registry()
    original_provider = registry._provider

    with (
        patch(
            "stackowl.embeddings.sentence_transformer_provider.SentenceTransformerProvider.create",
            new=AsyncMock(side_effect=RuntimeError("still no network")),
        ),
        pytest.raises(RuntimeError, match="still no network"),
    ):
        await registry.ensure_available()

    assert registry._is_semantic is False
    assert registry._provider is original_provider


async def test_available_and_unavailable_reason_reflect_semantic_state() -> None:
    degraded = _degraded_registry()
    assert degraded.available is False
    assert degraded.unavailable_reason is not None

    semantic = EmbeddingRegistry()
    semantic._provider = _FakeSemanticProvider()  # type: ignore[assignment]
    semantic._is_semantic = True
    assert semantic.available is True
    assert semantic.unavailable_reason is None


async def test_register_on_recycled_is_a_noop_callback_registration() -> None:
    registry = _degraded_registry()
    # Must not raise — matches ModelProvider's stateless no-op pattern (no
    # downstream dependents to notify today).
    registry.register_on_recycled(lambda: None)
