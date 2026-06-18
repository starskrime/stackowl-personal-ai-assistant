"""ADR-18 spike: validate embedding stack on current platform.

Run with: ``STACKOWL_RUN_SPIKES=1 uv run pytest tests/spikes/test_adr18_embedding.py -v``
(or pass ``--runspike`` to pytest, which sets the env var for you).

Skipped by default (CI collects but does not run). When run, this spike
benchmarks the SentenceTransformer load time, embedding throughput, and memory
footprint on the local platform, verifies the SQLite FTS5 fallback recall and
the always-available HashEmbeddingProvider, then writes a resolution document
to ``docs/adr-18-resolution.md`` summarising the findings.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import time
from collections.abc import AsyncIterator
from importlib import metadata
from pathlib import Path

import aiosqlite
import psutil
import pytest

log = logging.getLogger("stackowl.spike.adr18")

_SPIKE_ENABLED = os.environ.get("STACKOWL_RUN_SPIKES") == "1"
pytestmark = [
    pytest.mark.spike,
    pytest.mark.skipif(
        not _SPIKE_ENABLED,
        reason="spike — set STACKOWL_RUN_SPIKES=1 (or pass --runspike) to run",
    ),
]

_FIXTURES = Path(__file__).parent / "fixtures" / "adr18_relevance.json"
_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
_RESOLUTION_DOC = _DOCS_DIR / "adr-18-resolution.md"

_THRESHOLDS = {
    "load_time_s": 30.0,
    "embed_time_s": 5.0,
    "memory_delta_gb": 2.0,
}

# In-process measurement cache populated by the timing tests so that
# ``test_generate_resolution_doc`` can render real numbers instead of re-running
# the model. The dict is mutated by the individual tests in this module.
_RESULTS: dict[str, dict[str, float | bool | str]] = {}


# -- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
async def fixtures_payload() -> dict[str, object]:
    """Parse the labelled relevance fixture once per session."""
    log.debug("[spike.adr18] loading fixtures path=%s", _FIXTURES)
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
async def sentence_transformer_provider() -> AsyncIterator[object]:
    """Load SentenceTransformerProvider once per module and reuse across tests."""
    from stackowl.embeddings.sentence_transformer_provider import SentenceTransformerProvider

    provider = await SentenceTransformerProvider.create("all-MiniLM-L6-v2")
    try:
        yield provider
    finally:
        # SentenceTransformerProvider has no explicit close; rely on GC.
        del provider


# -- helpers ----------------------------------------------------------------


def _rss_bytes() -> int:
    return int(psutil.Process(os.getpid()).memory_info().rss)


def _record(stage: str, value: float, threshold: float, passed: bool, unit: str) -> None:
    _RESULTS[stage] = {
        "value": float(value),
        "threshold": float(threshold),
        "pass": bool(passed),
        "unit": unit,
    }
    log.info(
        "[spike.adr18] platform=%s stage=%s value=%.3f threshold=%.3f unit=%s pass=%s",
        platform.machine(),
        stage,
        value,
        threshold,
        unit,
        passed,
    )


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


# -- tests ------------------------------------------------------------------


async def test_sentence_transformer_load_time() -> None:
    """Cold-load the SentenceTransformer model and assert under threshold."""
    from stackowl.embeddings.sentence_transformer_provider import SentenceTransformerProvider

    start = time.monotonic()
    provider = await SentenceTransformerProvider.create("all-MiniLM-L6-v2")
    load_time = time.monotonic() - start

    passed = load_time < _THRESHOLDS["load_time_s"]
    _record("load", load_time, _THRESHOLDS["load_time_s"], passed, "s")

    assert provider.dimension > 0, "loaded model reported zero dimensions"
    assert load_time < _THRESHOLDS["load_time_s"], (
        f"model load took {load_time:.2f}s (threshold {_THRESHOLDS['load_time_s']:.1f}s)"
    )


async def test_sentence_transformer_embed_100_texts(
    sentence_transformer_provider: object,
    fixtures_payload: dict[str, object],
) -> None:
    """Embed 100 varied texts and assert under throughput threshold."""
    provider = sentence_transformer_provider  # type: ignore[assignment]
    corpus = fixtures_payload["corpus"]
    assert isinstance(corpus, list)

    # Build 100 varied texts by cycling the 20-item corpus with index suffixes
    # to defeat any internal caching the encoder may apply to identical strings.
    texts: list[str] = []
    for i in range(100):
        item = corpus[i % len(corpus)]
        assert isinstance(item, dict)
        text = str(item["text"])
        texts.append(f"{text} [variant {i}]")

    start = time.monotonic()
    vectors = await provider.embed(texts)  # type: ignore[attr-defined]
    embed_time = time.monotonic() - start

    passed = embed_time < _THRESHOLDS["embed_time_s"]
    _record("embed_100", embed_time, _THRESHOLDS["embed_time_s"], passed, "s")

    assert len(vectors) == 100
    assert all(len(v) == provider.dimension for v in vectors)  # type: ignore[attr-defined]
    assert embed_time < _THRESHOLDS["embed_time_s"], (
        f"embedding 100 texts took {embed_time:.2f}s (threshold {_THRESHOLDS['embed_time_s']:.1f}s)"
    )


async def test_sentence_transformer_memory_footprint(
    fixtures_payload: dict[str, object],
) -> None:
    """Measure RSS delta across a fresh load + embed and assert under threshold."""
    from stackowl.embeddings.sentence_transformer_provider import SentenceTransformerProvider

    corpus = fixtures_payload["corpus"]
    assert isinstance(corpus, list)
    sample_texts = [str(item["text"]) for item in corpus if isinstance(item, dict)]

    rss_before = _rss_bytes()
    provider = await SentenceTransformerProvider.create("all-MiniLM-L6-v2")
    await provider.embed(sample_texts)
    rss_after = _rss_bytes()

    delta_bytes = rss_after - rss_before
    delta_gb = delta_bytes / (1024**3)

    passed = delta_gb < _THRESHOLDS["memory_delta_gb"]
    _record("memory_delta", delta_gb, _THRESHOLDS["memory_delta_gb"], passed, "GB")

    # Keep the provider alive until measurement done; explicit drop helps GC.
    del provider

    assert delta_gb < _THRESHOLDS["memory_delta_gb"], (
        f"RSS delta {delta_gb:.2f}GB exceeds threshold {_THRESHOLDS['memory_delta_gb']:.1f}GB"
    )


async def test_fts5_fallback_recall(fixtures_payload: dict[str, object]) -> None:
    """Validate the SQLite FTS5 fallback layer can retrieve relevant docs."""
    corpus = fixtures_payload["corpus"]
    queries = fixtures_payload["queries"]
    assert isinstance(corpus, list) and isinstance(queries, list)

    total_hits = 0
    total_relevant = 0
    per_query: list[dict[str, float]] = []

    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE VIRTUAL TABLE fts USING fts5(text)")
        for item in corpus:
            assert isinstance(item, dict)
            rowid = int(str(item["id"])[1:])
            await db.execute(
                "INSERT INTO fts(rowid, text) VALUES (?, ?)",
                (rowid, str(item["text"])),
            )
        await db.commit()

        for q in queries:
            assert isinstance(q, dict)
            query_text = str(q["query"])
            relevant_ids = [str(rid) for rid in q["relevant_ids"]]  # type: ignore[union-attr]
            relevant_rowids = {int(rid[1:]) for rid in relevant_ids}

            # FTS5 MATCH expects an OR-joined token query for natural-language phrases.
            tokens = [tok for tok in query_text.split() if tok]
            match_expr = " OR ".join(tokens) if tokens else query_text

            async with db.execute(
                "SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT 10",
                (match_expr,),
            ) as cursor:
                rows = await cursor.fetchall()

            retrieved_rowids = {int(r[0]) for r in rows}
            hits = len(retrieved_rowids & relevant_rowids)
            recall = hits / max(len(relevant_rowids), 1)
            total_hits += hits
            total_relevant += len(relevant_rowids)
            per_query.append({"hits": float(hits), "relevant": float(len(relevant_rowids)), "recall": recall})

    overall_recall = total_hits / max(total_relevant, 1)
    passed = overall_recall > 0.0
    _record("fts5_recall@10", overall_recall, 0.0, passed, "ratio")

    log.info(
        "[spike.adr18] fts5 detail per_query=%s",
        json.dumps(per_query),
    )
    assert overall_recall > 0.0, (
        f"FTS5 fallback failed to find any relevant documents (hits={total_hits}, relevant={total_relevant})"
    )


async def test_hash_provider_always_available() -> None:
    """The hash provider is the final fallback and must always work."""
    from stackowl.embeddings.hash_provider import HashEmbeddingProvider

    provider = HashEmbeddingProvider()
    texts = [f"text number {i}" for i in range(10)]
    vectors = await provider.embed(texts)

    assert len(vectors) == 10
    assert all(len(v) == provider.dimension for v in vectors)
    _record("hash_fallback", float(provider.dimension), float(provider.dimension), True, "dim")


async def test_generate_resolution_doc() -> None:
    """Render the resolution doc from the accumulated measurements."""
    # If a prior test failed before recording, compute minimal values inline so
    # the doc still reflects something real instead of TBDs.
    if "load" not in _RESULTS or "embed_100" not in _RESULTS or "memory_delta" not in _RESULTS:
        from stackowl.embeddings.sentence_transformer_provider import SentenceTransformerProvider

        rss_before = _rss_bytes()
        start = time.monotonic()
        provider = await SentenceTransformerProvider.create("all-MiniLM-L6-v2")
        load_time = time.monotonic() - start
        _record("load", load_time, _THRESHOLDS["load_time_s"], load_time < _THRESHOLDS["load_time_s"], "s")

        texts = [f"resolution-doc sample text {i}" for i in range(100)]
        start = time.monotonic()
        await provider.embed(texts)
        embed_time = time.monotonic() - start
        _record(
            "embed_100",
            embed_time,
            _THRESHOLDS["embed_time_s"],
            embed_time < _THRESHOLDS["embed_time_s"],
            "s",
        )

        rss_after = _rss_bytes()
        delta_gb = (rss_after - rss_before) / (1024**3)
        _record(
            "memory_delta",
            delta_gb,
            _THRESHOLDS["memory_delta_gb"],
            delta_gb < _THRESHOLDS["memory_delta_gb"],
            "GB",
        )

    # The FTS5 and hash measurements are optional in the doc — they may not be
    # present if those tests were deselected. Render whatever we have.
    semantic_ok = bool(
        _RESULTS.get("load", {}).get("pass")
        and _RESULTS.get("embed_100", {}).get("pass")
        and _RESULTS.get("memory_delta", {}).get("pass")
    )
    semantic_default = "true" if semantic_ok else "false"
    decision = (
        "SentenceTransformer (`all-MiniLM-L6-v2`) is viable on this platform."
        if semantic_ok
        else "SentenceTransformer is NOT viable on this platform — fall back to FTS5 BM25 + hash."
    )

    machine = platform.machine()
    system = platform.system()
    release = platform.release()
    py_version = sys.version.split()[0]
    st_version = _pkg_version("sentence-transformers")
    np_version = _pkg_version("numpy")

    def _row(stage: str) -> str:
        rec = _RESULTS.get(stage)
        if rec is None:
            return f"| {stage} | n/a | n/a | n/a | skipped |"
        unit = rec["unit"]
        value = rec["value"]
        threshold = rec["threshold"]
        passed = "PASS" if rec["pass"] else "FAIL"
        return f"| {stage} | {value:.3f} | {threshold:.3f} | {unit} | {passed} |"

    lines = [
        "Status: Resolved (Story 6.1)",
        "",
        "# ADR-18: Embedding Stack Validation",
        "",
        f"Generated by `tests/spikes/test_adr18_embedding.py` on {platform.node()}.",
        "",
        "## Decision",
        "",
        decision,
        "",
        "## Benchmark Results",
        "",
        "| Stage | Value | Threshold | Unit | Result |",
        "| --- | --- | --- | --- | --- |",
        _row("load"),
        _row("embed_100"),
        _row("memory_delta"),
        _row("fts5_recall@10"),
        _row("hash_fallback"),
        "",
        "## Fallback Chain",
        "",
        "1. `SentenceTransformerProvider` (`all-MiniLM-L6-v2`) — semantic embeddings, ~384-d vectors.",
        "2. SQLite FTS5 BM25 — lexical recall layer used when semantic embeddings are unavailable or low-confidence.",
        "3. `HashEmbeddingProvider` — deterministic 384-d hash projection, always available with zero external dependencies.",
        "",
        "## Platform Notes",
        "",
        f"- `platform.machine()`: `{machine}`",
        f"- `platform.system()`: `{system}` ({release})",
        f"- Python: `{py_version}`",
        f"- `sentence-transformers`: `{st_version}`",
        f"- `numpy`: `{np_version}`",
        "",
        "## Settings Defaults",
        "",
        f"```yaml",
        f"semantic_search_enabled: {semantic_default}",
        f"embedding_model: all-MiniLM-L6-v2",
        f"```",
        "",
    ]

    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _RESOLUTION_DOC.write_text("\n".join(lines), encoding="utf-8")

    log.info(
        "[spike.adr18] resolution doc written path=%s semantic_default=%s",
        _RESOLUTION_DOC,
        semantic_default,
    )

    # Sanity-check the file was written and contains the resolved status header.
    rendered = _RESOLUTION_DOC.read_text(encoding="utf-8")
    assert rendered.startswith("Status: Resolved (Story 6.1)")
    assert "## Decision" in rendered
    assert "## Benchmark Results" in rendered
    assert "## Fallback Chain" in rendered
    assert "## Platform Notes" in rendered
    assert "## Settings Defaults" in rendered
