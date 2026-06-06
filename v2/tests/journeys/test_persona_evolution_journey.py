"""Persona-evolution journeys (persona-evo T6) — prove the feature end-to-end.

Three user-outcome journeys, mocking ONLY the AI provider + using a
deterministic embedder.  Everything load-bearing is the REAL production code:
``FactPromoter`` (+ its PE5 embed-on-promote), ``SqliteMemoryBridge``,
``EvolutionCoordinator`` (with the REAL ``bound_dna`` governor), ``hydrate_dna``
and ``DNAPromptInjector``.

(A) Cross-session recall — session A ``remember``s a fact WITHOUT a vector →
    a deterministic promote pass → session B recalls it SEMANTICALLY.  Proves
    capture→promote→recall closes AND regression-guards PE5 (the promoter
    computes the missing embedding so the fact becomes semantically recallable).

(B) DNA loop (live + survives restart) — one evolution batch (mocked LLM
    deltas) mutates the live registry DNA bounded by the governor; a FRESH
    ``OwlRegistry`` + ``hydrate_dna`` (simulated restart) recovers the persisted
    bounded value AND ``DNAPromptInjector.inject`` now emits the evolved
    directive.  Proves persist→live + persist→hydrate end-to-end.

(C) Slow-poison floor holds — several batches all pushing ``challenge_level``
    toward 0 can NEVER drop it below ``TRAIT_FLOOR`` (0.3): the persona never
    enters a "no pushback" state.  Driven through the REAL governor.

Harnesses reused from ``tests/test_story_4_3.py`` (evolution: MockProvider +
register_mock + OwlRegistry + _seed_messages + EvolutionCoordinator +
TestModeGuard.deactivate + SQLite asserts) and ``tests/test_story_6_3.py``
(memory: db fixture + _insert_staged + deterministic embedder stub + FactPromoter
+ SqliteMemoryBridge).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.fact_promoter import FactPromoter
from stackowl.memory.lancedb_helpers import SearchResult
from stackowl.memory.models import StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge
from stackowl.memory.sqlite_helpers import cosine_similarity
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_hydrator import hydrate_dna
from stackowl.owls.dna_injector import DNAPromptInjector
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.evolution_limits import TRAIT_FLOOR
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.providers.mock_provider import MockProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures & doubles
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the real embedder/promoter/lancedb-spy run inside the test process.

    Same pattern as story_6_3: the memory I/O paths gate on TestModeGuard, and a
    deterministic stub IS the live replacement, so the guard is neutralized.
    """
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "persona_evo_journey.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class _StubEmbeddingProvider:
    """Deterministic embedder (mirrors story_6_3) — sum-of-ords seed → vector."""

    def __init__(self, dim: int = 8, name: str = "stub-embed") -> None:
        self._dim = dim
        self._name = name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            seed = (sum(ord(c) for c in text) % 100) / 100.0 or 0.1
            out.append([seed * (i + 1) for i in range(self._dim)])
        return out

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return True

    async def health_check(self) -> Any:  # pragma: no cover — unused
        return None


class _StubEmbeddingRegistry:
    """Registry stub exposing ``.get()`` / ``.is_semantic`` like the real one."""

    def __init__(self, provider: _StubEmbeddingProvider) -> None:
        self._provider = provider

    def get(self) -> _StubEmbeddingProvider:
        return self._provider

    @property
    def is_semantic(self) -> bool:
        return True


class _SpyLanceDB:
    """In-memory ANN spy with the same async surface the bridge/promoter use.

    Deterministic cosine ranking — no real ``lancedb`` dependency (flaky on the
    Jetson box per the e4_s1 smoke). It records every upsert so the journey can
    assert PE5 actually upserted a COMPUTED vector for the miner-staged fact.
    """

    def __init__(self) -> None:
        self.upserts: list[tuple[str, list[float]]] = []
        self._vectors: dict[str, list[float]] = {}

    async def upsert(
        self, fact_id: str, embedding: list[float], metadata: dict[str, Any]
    ) -> None:
        self.upserts.append((fact_id, list(embedding)))
        self._vectors[fact_id] = list(embedding)

    async def search(
        self, query_embedding: list[float], limit: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        scored: list[tuple[float, str]] = []
        for fact_id, vec in self._vectors.items():
            sim = cosine_similarity(query_embedding, vec)
            if sim is not None:
                scored.append((sim, fact_id))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [
            SearchResult(fact_id=fid, score=score, metadata={})
            for score, fid in scored[:limit]
        ]

    async def delete(self, fact_id: str) -> None:  # pragma: no cover — unused here
        self._vectors.pop(fact_id, None)


def _manifest(name: str, dna: OwlDNA | None = None) -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role="analyst",
        system_prompt="Be helpful and accurate.",
        model_tier="fast",
        dna=dna if dna is not None else OwlDNA(),
    )


def _job(job_id: str) -> Job:
    return Job(
        job_id=job_id,
        handler_name="evolution_batch",
        schedule="*/10 * * * *",
        idempotency_key=job_id,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
    )


async def _seed_messages(db: DbPool, owl_name: str, count: int) -> None:
    """Seed ``count`` user turns owned by ``owl_name`` (mirrors story_4_3)."""
    conv_id = uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO conversations (id, session_id, owl_name, started_at, message_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (conv_id, f"sess-{owl_name}-{conv_id[:6]}", owl_name, now, count),
    )
    for i in range(count):
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, conv_id, "user", f"sample message {i}", now),
        )


async def _persisted_dna(db: DbPool, owl_name: str) -> dict[str, float]:
    rows = await db.fetch_all(
        "SELECT challenge_level, verbosity, curiosity, formality, creativity, "
        "precision FROM owl_dna WHERE owl_name = ?",
        (owl_name,),
    )
    assert rows, f"no persisted owl_dna row for {owl_name}"
    return {k: float(v) for k, v in rows[0].items()}


async def _run_batch(
    db: DbPool, owl_registry: OwlRegistry, deltas_json: str, *, job_id: str
) -> None:
    """Run one REAL evolution batch with a mocked LLM returning ``deltas_json``.

    Mirrors story_4_3's coordinator harness: register a fast-tier MockProvider,
    deactivate TestModeGuard around the (mock) LLM call, execute the job. The
    governor (bound_dna), persist, and live-overlay are all REAL.
    """
    provider_registry = ProviderRegistry()
    provider_registry.register_mock(
        "mock-fast", MockProvider(name="mock-fast", canned_text=deltas_json), tier="fast"
    )
    coordinator = EvolutionCoordinator(
        db, provider_registry, owl_registry, evolution_batch_size=3
    )
    was_active = TestModeGuard.is_active()
    TestModeGuard.deactivate()
    try:
        result = await coordinator.execute(_job(job_id))
    finally:
        if was_active:
            TestModeGuard.activate()
    assert result.success is True, result.error


# ---------------------------------------------------------------------------
# (A) Cross-session recall — capture → promote → SEMANTIC recall (PE5 guard)
# ---------------------------------------------------------------------------


async def test_cross_session_fact_recall(db: DbPool) -> None:
    embedder = _StubEmbeddingProvider(dim=8)
    embed_registry = _StubEmbeddingRegistry(embedder)
    spy_lance = _SpyLanceDB()

    # --- Session A: stage a fact the miner way — NO vector at staging time.
    fact_text = "the production deploy key lives in vault path alpha-7"
    bridge_a = SqliteMemoryBridge(
        db, embedding_registry=embed_registry, lancedb=spy_lance  # type: ignore[arg-type]
    )
    staged = StagedFact(
        fact_id=str(uuid.uuid4()),
        content=fact_text,
        source_type="manual",
        source_ref="sess-A",
        confidence=1.0,
        reinforcement_count=3,
        embedding=None,          # miner-style: vector missing at stage time
        embedding_model=None,
    )
    await bridge_a.stage(staged)
    staged_rows = await db.fetch_all(
        "SELECT embedding FROM staged_facts WHERE fact_id = ?", (staged.fact_id,)
    )
    assert staged_rows[0]["embedding"] is None  # confirm: staged WITHOUT a vector

    # --- Deterministic promote pass (NOT the scheduler; settle window = 0).
    # The promoter holds the embedding_registry → PE5 computes the missing vector
    # and upserts it into the (spy) ANN store so the fact becomes recallable.
    promoter = FactPromoter(
        db,
        confidence_threshold=0.8,
        reinforcement_required=3,
        lancedb=spy_lance,  # type: ignore[arg-type]
        embedding_registry=embed_registry,  # type: ignore[arg-type]
        settle_minutes=0,
    )
    promoted = await promoter.promote_eligible()
    assert promoted == 1

    # PE5 proof at the seam: the promoter computed + upserted a real vector.
    assert spy_lance.upserts, "PE5 regression: promoter never upserted a vector"
    upserted_id, upserted_vec = spy_lance.upserts[0]
    assert upserted_id == staged.fact_id
    expected_vec = (await embedder.embed([fact_text]))[0]
    assert upserted_vec == pytest.approx(expected_vec)

    # The committed fact also persisted the computed embedding to SQLite.
    committed_rows = await db.fetch_all(
        "SELECT embedding, embedding_model FROM committed_facts WHERE fact_id = ?",
        (staged.fact_id,),
    )
    assert committed_rows and committed_rows[0]["embedding"]  # non-empty blob
    assert committed_rows[0]["embedding_model"] == "stub-embed"

    # --- Session B: a FRESH bridge (new "session") recalls SEMANTICALLY.
    bridge_b = SqliteMemoryBridge(
        db, embedding_registry=embed_registry, lancedb=spy_lance  # type: ignore[arg-type]
    )
    records = await bridge_b.recall("where is the deploy key stored", limit=5)
    assert records, "cross-session recall returned nothing"
    assert any(r.fact_id == staged.fact_id for r in records)
    assert any("vault path alpha-7" in r.content for r in records)


# ---------------------------------------------------------------------------
# (B) DNA loop — live mutation (governor-bounded) + survives a restart
# ---------------------------------------------------------------------------


async def test_dna_evolves_live_and_survives_restart(db: DbPool) -> None:
    owl = "nova"
    # Start curiosity near the top of the band so ONE governor-capped +0.05 batch
    # crosses the injector's 0.7 high threshold (0.69 → 0.74) — making the
    # evolved directive observable end-to-end.
    start_dna = OwlDNA(curiosity=0.69)
    live_registry = OwlRegistry()
    live_registry.register(_manifest(owl, dna=start_dna))
    await _seed_messages(db, owl, count=3)

    injector = DNAPromptInjector()
    manifest_before = live_registry.get(owl)
    # Pre-condition: at 0.69 the high-curiosity directive is NOT present yet.
    assert "clarifying" not in injector.inject(manifest_before, manifest_before.dna).lower()

    # --- One REAL evolution batch: LLM proposes a big +curiosity; governor caps
    # to +0.05 (0.69 → 0.74), persists FIRST, then live-overlays the registry.
    await _run_batch(
        db,
        live_registry,
        '{"curiosity": 0.5, "challenge_level": 0.0, "verbosity": 0.0, '
        '"formality": 0.0, "creativity": 0.0, "precision": 0.0}',
        job_id="job-B",
    )

    # LIVE: the registry DNA changed in place, bounded by MAX_DELTA (0.05).
    live_dna = live_registry.get(owl).dna
    assert live_dna.curiosity == pytest.approx(0.74)

    # PERSISTED == LIVE (DB is source of truth).
    persisted = await _persisted_dna(db, owl)
    assert persisted["curiosity"] == pytest.approx(0.74)

    # --- Simulated RESTART: a brand-new registry with NEUTRAL authored DNA, then
    # hydrate from the SAME db. The fresh owl must recover the persisted value.
    fresh_registry = OwlRegistry()
    fresh_registry.register(_manifest(owl, dna=OwlDNA()))  # neutral 0.5 at boot
    assert fresh_registry.get(owl).dna.curiosity == pytest.approx(0.5)

    hydrated = await hydrate_dna(fresh_registry, db)
    assert hydrated == 1
    fresh_manifest = fresh_registry.get(owl)
    assert fresh_manifest.dna.curiosity == pytest.approx(0.74)  # == persisted bounded value

    # The injector now emits the evolved high-curiosity directive for the
    # hydrated owl — the personality change is visible after restart.
    injected = injector.inject(fresh_manifest, fresh_manifest.dna)
    assert "clarifying" in injected.lower()
    assert injected != fresh_manifest.system_prompt  # differs from neutral-DNA output


# ---------------------------------------------------------------------------
# (C) Slow-poison floor holds — REAL governor, many batches
# ---------------------------------------------------------------------------


async def test_slow_poison_floor_holds_over_many_batches(db: DbPool) -> None:
    owl = "warden"
    live_registry = OwlRegistry()
    live_registry.register(_manifest(owl, dna=OwlDNA()))  # challenge_level = 0.5
    await _seed_messages(db, owl, count=3)

    injector = DNAPromptInjector()
    # Each batch: the LLM relentlessly pushes challenge_level toward 0. The REAL
    # governor caps the move to -0.05/batch and holds the floor. 10 uncapped
    # would drive it to 0.0; with the floor it can never breach 0.3.
    poison = (
        '{"challenge_level": -0.5, "verbosity": 0.0, "curiosity": 0.0, '
        '"formality": 0.0, "creativity": 0.0, "precision": 0.0}'
    )
    n_batches = 10
    for i in range(n_batches):
        await _run_batch(db, live_registry, poison, job_id=f"job-C-{i}")
        cl = live_registry.get(owl).dna.challenge_level
        # Invariant must hold at EVERY step, not merely at the end.
        assert cl >= TRAIT_FLOOR, f"floor breached at batch {i}: challenge_level={cl}"

    final_cl = live_registry.get(owl).dna.challenge_level
    assert final_cl == pytest.approx(TRAIT_FLOOR)  # parked exactly on the floor

    # Persisted value also respects the floor (DB is source of truth).
    persisted = await _persisted_dna(db, owl)
    assert persisted["challenge_level"] >= TRAIT_FLOOR
    assert persisted["challenge_level"] == pytest.approx(TRAIT_FLOOR)

    # SECURITY OUTCOME: challenge_level never entered the injector's LOW band
    # (< 0.3), so the persona is never disarmed into a "no pushback" state.
    # (challenge_level has no low-directive, but staying >= floor keeps it out of
    # the low band entirely — the floor IS the guarantee.)
    final_manifest = live_registry.get(owl)
    _ = injector.inject(final_manifest, final_manifest.dna)  # must not raise
    assert final_manifest.dna.challenge_level >= TRAIT_FLOOR
