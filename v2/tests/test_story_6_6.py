"""Story 6.6 (part A) — ContradictionDetector, migration 0017, models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.memory.contradiction_detector import (
    ContradictionDetector,
    ContradictionReport,
)
from stackowl.memory.dream_worker import DreamWorkerCheckpoint

from tests._story_6_6_helpers import (  # noqa: F401 — re-exports
    db,
    no_test_mode_guard,
    record,
    staged,
)


# ---------------------------------------------------------------------------
# ContradictionDetector
# ---------------------------------------------------------------------------


async def test_detector_near_duplicate_flagged() -> None:
    """T1 — two near-identical embeddings flagged as near-duplicate."""
    detector = ContradictionDetector()
    a = staged("a", embedding=[1.0, 0.0, 0.0])
    b = staged("b", embedding=[1.0, 0.001, 0.0])
    reports = await detector.detect([a, b])
    assert len(reports) == 1
    assert reports[0].explanation == "near-duplicate"
    assert reports[0].confidence >= 0.95


async def test_detector_potential_contradiction_flagged() -> None:
    """T2 — two high-sim different-source facts flagged as contradiction."""
    detector = ContradictionDetector()
    # cos([1,0], [0.9,0.43589]) ≈ 0.9 — sits in [0.85, 0.95) range
    a = staged("a", source_type="conversation", embedding=[1.0, 0.0])
    b = staged("b", source_type="manual", embedding=[0.9, 0.43589])
    reports = await detector.detect([a, b])
    assert len(reports) == 1
    assert reports[0].explanation == "potential-contradiction"
    assert 0.85 <= reports[0].confidence < 0.95


async def test_detector_skips_facts_without_embeddings() -> None:
    """T3 — facts without embeddings are skipped (no crash)."""
    detector = ContradictionDetector()
    a = staged("a", embedding=None)
    b = staged("b", embedding=None)
    c = staged("c", embedding=[1.0, 0.0])
    reports = await detector.detect([a, b, c])
    # Only one embedded fact remains so no pairs are formed
    assert reports == []


async def test_detector_returns_empty_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T4 — returns [] when an unexpected exception fires."""
    detector = ContradictionDetector()

    def boom(self: object, facts: list[Any]) -> list[Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(ContradictionDetector, "_filter_embedded", boom)
    reports = await detector.detect([staged("a", embedding=[1.0])])
    assert reports == []


def test_contradiction_report_is_frozen() -> None:
    """T5 — ContradictionReport rejects attribute mutation (Pydantic frozen)."""
    report = ContradictionReport(
        fact_id_a="a", fact_id_b="b", explanation="x", confidence=0.9
    )
    with pytest.raises(ValidationError):
        report.fact_id_a = "z"  # type: ignore[misc]


async def test_detector_low_similarity_not_flagged() -> None:
    """T16 — pairs below 0.85 are NOT flagged."""
    detector = ContradictionDetector()
    a = staged("a", embedding=[1.0, 0.0, 0.0])
    b = staged("b", embedding=[0.0, 1.0, 0.0])  # orthogonal → cos = 0
    reports = await detector.detect([a, b])
    assert reports == []


async def test_detector_works_with_memory_record_input() -> None:
    """T19 — detector accepts MemoryRecord objects (not only StagedFact)."""
    detector = ContradictionDetector()
    a = record("a", source_type="conversation", embedding=[1.0, 0.0])
    b = record("b", source_type="manual", embedding=[0.95, 0.05])
    reports = await detector.detect([a, b])
    assert len(reports) == 1
    assert reports[0].explanation == "potential-contradiction"


# ---------------------------------------------------------------------------
# Migration 0017 + DreamWorkerCheckpoint shape
# ---------------------------------------------------------------------------


def test_migration_0017_file_exists() -> None:
    """T13 — migration file exists at the expected path."""
    path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
        / "0017_dreamworker.sql"
    )
    assert path.exists(), f"missing migration: {path}"


def test_migration_count_is_17(tmp_path: Path) -> None:
    """T14 — MigrationRunner discovers and runs EVERY migration .sql file.

    Name kept historical for log searchability. The expected count is now
    derived dynamically from the actual ``.sql`` files on disk (no more manual
    bumps on every new migration); the invariant under test is that the runner
    discovers all migration files with none silently skipped.
    """
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    runner = MigrationRunner(db_path=tmp_path / "count.db")
    results = runner.run()
    assert len(results) == expected


def test_dream_worker_checkpoint_field_types() -> None:
    """T15 — DreamWorkerCheckpoint fields are correctly typed and frozen."""
    cp = DreamWorkerCheckpoint(
        run_id="r",
        started_at="2026-01-01T00:00:00+00:00",
        phase="contradiction",
        facts_processed=1,
        facts_promoted=2,
        facts_pruned=3,
        contradictions_found=4,
    )
    assert cp.run_id == "r"
    assert cp.phase == "contradiction"
    assert cp.facts_processed == 1
    assert cp.facts_promoted == 2
    assert cp.facts_pruned == 3
    assert cp.contradictions_found == 4
    with pytest.raises(ValidationError):
        cp.phase = "promotion"  # type: ignore[misc]
