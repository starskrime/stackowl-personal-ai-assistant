"""Story 3.6 tests — EmbeddingProvider, HashProvider, EmbeddingRegistry, B8 boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def test_hash_provider_is_local() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    p = HashEmbeddingProvider()
    assert p.is_local is True


async def test_hash_provider_embed_dimension() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    p = HashEmbeddingProvider()
    results = await p.embed(["hello world"])
    assert len(results) == 1
    assert len(results[0]) == 384


async def test_hash_provider_deterministic() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    p = HashEmbeddingProvider()
    r1 = await p.embed(["test text"])
    r2 = await p.embed(["test text"])
    assert r1 == r2


async def test_hash_provider_different_texts() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    p = HashEmbeddingProvider()
    results = await p.embed(["hello", "world"])
    assert results[0] != results[1]


async def test_hash_provider_model_name() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    assert HashEmbeddingProvider().model_name == "hash-v1-384d"


async def test_hash_provider_dimension_property() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    assert HashEmbeddingProvider().dimension == 384


async def test_hash_provider_batch_preserves_order() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    p = HashEmbeddingProvider()
    singles = [await p.embed([t]) for t in ["alpha", "beta", "gamma"]]
    batched = await p.embed(["alpha", "beta", "gamma"])
    assert batched[0] == singles[0][0]
    assert batched[1] == singles[1][0]
    assert batched[2] == singles[2][0]


async def test_hash_provider_health_check_ok() -> None:
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    h = await HashEmbeddingProvider().health_check()
    assert h.status == "ok"
    assert h.name == "embedding_hash-v1-384d"


async def test_embedding_registry_falls_back_to_hash() -> None:
    """Registry falls back to hash when SentenceTransformer not available."""
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider
    from stackowl.embeddings.registry import EmbeddingRegistry

    with patch(
        "stackowl.embeddings.sentence_transformer_provider.SentenceTransformerProvider.create",
        new=AsyncMock(side_effect=ImportError("no model")),
    ):
        registry = await EmbeddingRegistry.create()
        assert isinstance(registry.get(), HashEmbeddingProvider)
        assert registry.is_semantic is False


async def test_embedding_registry_health_degraded_on_hash() -> None:
    from stackowl.embeddings.registry import EmbeddingRegistry

    with patch(
        "stackowl.embeddings.sentence_transformer_provider.SentenceTransformerProvider.create",
        new=AsyncMock(side_effect=ImportError("no model")),
    ):
        registry = await EmbeddingRegistry.create()
        health = await registry.health_check()
        assert health.status == "degraded"
        assert health.message is not None
        assert "stackowl models pull" in health.message


async def test_embedding_registry_get_without_create_returns_hash() -> None:
    """Calling get() on a bare registry must lazily install hash provider."""
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider
    from stackowl.embeddings.registry import EmbeddingRegistry

    registry = EmbeddingRegistry()
    provider = registry.get()
    assert isinstance(provider, HashEmbeddingProvider)
    assert registry.is_semantic is False


async def test_sentence_transformer_embed_requires_loaded_model() -> None:
    """embed() raises if model not loaded via create()."""
    import pytest

    from stackowl.embeddings.sentence_transformer_provider import (
        SentenceTransformerProvider,
    )

    p = SentenceTransformerProvider()
    with pytest.raises(RuntimeError):
        await p.embed(["text"])


async def test_sentence_transformer_is_local() -> None:
    from stackowl.embeddings.sentence_transformer_provider import (
        SentenceTransformerProvider,
    )

    assert SentenceTransformerProvider().is_local is True


async def test_sentence_transformer_selfheals_via_download_on_cache_miss(
    monkeypatch: object,
) -> None:
    """Cache-miss + no operator override → ONE retry with network allowed.

    This is the self-heal path: `stackowl models pull` downloads via the same
    `SentenceTransformer(...)` call, so a missing cache should self-heal the
    same way instead of degrading straight to the hash fallback.
    """
    import os
    from unittest.mock import MagicMock

    from stackowl.embeddings.sentence_transformer_provider import (
        SentenceTransformerProvider,
    )

    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)  # type: ignore[attr-defined]

    calls: list[dict[str, str | None]] = []

    def fake_ctor(*args: object, **kwargs: object) -> object:
        calls.append(
            {
                "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
                "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
            }
        )
        if len(calls) == 1:
            raise OSError("model not found in local cache")
        mock_model = MagicMock()
        mock_model.get_embedding_dimension.return_value = 384
        return mock_model

    with patch("sentence_transformers.SentenceTransformer", side_effect=fake_ctor):
        provider = await SentenceTransformerProvider.create("fake-model")

    assert len(calls) == 2, "expected exactly one retry after the cache-miss failure"
    assert calls[0]["HF_HUB_OFFLINE"] == "1"  # first attempt: our offline default
    assert calls[1]["HF_HUB_OFFLINE"] is None  # retry: network allowed
    assert calls[1]["TRANSFORMERS_OFFLINE"] is None
    assert provider.dimension == 384


async def test_sentence_transformer_respects_operator_forced_offline(
    monkeypatch: object,
) -> None:
    """Operator-set HF_HUB_OFFLINE=1 must NOT trigger the self-heal retry."""
    import pytest

    from stackowl.embeddings.sentence_transformer_provider import (
        SentenceTransformerProvider,
    )

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")  # type: ignore[attr-defined]
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)  # type: ignore[attr-defined]

    calls: list[int] = []

    def fake_ctor(*args: object, **kwargs: object) -> object:
        calls.append(1)
        raise OSError("model not found in local cache")

    with (
        patch("sentence_transformers.SentenceTransformer", side_effect=fake_ctor),
        pytest.raises(OSError),
    ):
        await SentenceTransformerProvider.create("fake-model")

    assert len(calls) == 1, "operator-forced offline mode must not trigger a network retry"


def test_b8_boundary_passes() -> None:
    """B8 finds no forbidden imports in embeddings/."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "boundaries" / "b8.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, f"B8 failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "B8 PASS" in result.stdout


def test_b8_detects_forbidden_import(tmp_path: Path, monkeypatch: object) -> None:
    """B8 rejects a fake embeddings file that imports httpx."""
    fake_emb = tmp_path / "src" / "stackowl" / "embeddings"
    fake_emb.mkdir(parents=True)
    (fake_emb / "__init__.py").write_text("", encoding="utf-8")
    (fake_emb / "bad.py").write_text("import httpx\n", encoding="utf-8")

    # Copy b8.py into a parallel scripts/boundaries/ next to fake src/
    scripts_dir = tmp_path / "scripts" / "boundaries"
    scripts_dir.mkdir(parents=True)
    real_b8 = Path(__file__).resolve().parent.parent / "scripts" / "boundaries" / "b8.py"
    (scripts_dir / "b8.py").write_text(real_b8.read_text(), encoding="utf-8")

    result = subprocess.run([sys.executable, str(scripts_dir / "b8.py")], capture_output=True, text=True)
    assert result.returncode == 1
    assert "B8 FAIL" in result.stdout
    assert "httpx" in result.stdout


def test_models_cli_registered() -> None:
    """The `stackowl models` subcommand group must be wired up."""
    from stackowl.cli.app import app

    names = {
        info.name
        for info in app.registered_groups  # type: ignore[attr-defined]
    }
    assert "models" in names
