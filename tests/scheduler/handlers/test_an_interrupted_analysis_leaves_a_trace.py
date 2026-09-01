"""An RCA killed mid-flight must not look like one that never ran.

MEASURED 2026-09-01 over the retained logs:

* ``[rca] staged.analyze: entry`` **462** times against ``exit`` **355** — 107
  analyses (23%) never finished;
* **85 of those 107 had a RESTART as their next event**;
* **232 boots in four days**, one every ~25 minutes, because CodeWatcher
  exec-replaces the core on every commit.

A staged RCA budgets up to 960 seconds. Work that takes sixteen minutes cannot
survive a process replaced every twenty-five, and roughly one in five never did.
That is not an edge case — a self-updating platform restarting constantly is the
NORMAL condition here, and the analysis was built as though it were not.

NOTHING RECORDED IT, WHICH IS THE ACTUAL DEFECT. ``record_diagnosis`` is written
on COMPLETION only. An interrupted analysis therefore left the signature looking
"never diagnosed": the next tick started the identical analysis from zero, spent
another ~140,000 tokens, and was interrupted again. 85 x 140k is roughly 12
million tokens that produced no verdict, no record, and no way to notice — the
platform could not tell "we tried and were killed" from "we never tried".

THE START MARKER SUPPRESSES, AND THAT IS THE POINT rather than a side effect.
Four days of evidence say an analysis interrupted once will be interrupted
again; re-entering it every ten minutes is the furnace. The 24h expiry still
applies so a genuinely-needed analysis is retried tomorrow, and the incident
itself is still DETECTED every tick — only the expensive re-analysis is held.

WHAT THE SAME CAUSE REACHES. Any long-running background work on this platform:
it has no durable "I started" anywhere, so a restart is indistinguishable from
never having begun. The RCA is where it was measurable because it is the
expensive one.

NOT FIXED HERE: making the analysis itself RESUMABLE. That is the larger answer
and it is an architecture question, not a fourth attempt at bookkeeping. What
ships is the measurement that makes it a decision rather than a guess —
``interrupted_diagnoses`` now counts exactly how much work is being lost.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.incident_escalation import (
    DIAGNOSED_EVENT,
    STARTED_EVENT,
    interrupted_diagnoses,
    recently_diagnosed,
    record_diagnosis,
    record_diagnosis_started,
)
from tests._schema_template import seed_schema

pytestmark = pytest.mark.asyncio

_NOW = 1_788_000_000.0
_SIG = "outcome:web_fetch:stop"


@pytest.fixture
async def pool(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    path = tmp_path / "ledger.db"
    db = DbPool(db_path=path)
    await db.open()
    seed_schema(path)
    yield db
    await db.close()


async def test_a_started_analysis_suppresses_a_repeat(pool) -> None:  # noqa: ANN001
    """The furnace, stopped. Before this, an interrupted RCA left no row and the
    next tick spent another ~140,000 tokens on the identical analysis."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert _SIG in await recently_diagnosed(pool, now=_NOW + 60.0)


async def test_a_completed_analysis_still_suppresses(pool) -> None:  # noqa: ANN001
    """The behaviour that already existed must not regress."""
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-a", verified=False, now=_NOW,
    )
    assert _SIG in await recently_diagnosed(pool, now=_NOW + 60.0)


async def test_suppression_still_expires(pool) -> None:  # noqa: ANN001
    """A start marker must not become a permanent ban — a problem still present
    tomorrow deserves a fresh look, and self-healing that never retries is the
    failure mode this whole arc exists to prevent."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert _SIG not in await recently_diagnosed(pool, now=_NOW + 25 * 3600.0)


# --------------------------------------------------------------------------- #
# The measurement that was impossible before                                   #
# --------------------------------------------------------------------------- #


async def test_a_start_with_no_finish_is_counted_as_interrupted(pool) -> None:  # noqa: ANN001
    """The 85-of-462 case, which previously left no trace at all."""
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 60.0) == [_SIG]


async def test_a_completed_analysis_is_not_interrupted(pool) -> None:  # noqa: ANN001
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-a", verified=True, now=_NOW + 10.0,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 60.0) == []


async def test_a_completion_only_counts_if_it_came_AFTER_the_start(pool) -> None:  # noqa: ANN001
    """Yesterday's success must not mask today's interruption, or the count
    silently under-reports exactly when the platform is worst."""
    await record_diagnosis(
        pool, signature=_SIG, incident_id="incident-old", verified=True, now=_NOW,
    )
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-new", now=_NOW + 100.0,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 200.0) == [_SIG]


async def test_an_old_interruption_falls_out_of_the_window(pool) -> None:  # noqa: ANN001
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )
    assert await interrupted_diagnoses(pool, now=_NOW + 25 * 3600.0) == []


async def test_a_broken_ledger_read_says_it_cannot_tell(pool, monkeypatch) -> None:  # noqa: ANN001
    """Empty must mean "cannot tell", never "none" — a metric that reports zero
    on a failed query is how a regression gets called a fix."""

    async def _boom(*a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")

    monkeypatch.setattr(pool, "fetch_all", _boom)
    assert await interrupted_diagnoses(pool, now=_NOW) == []
    assert await recently_diagnosed(pool, now=_NOW) == set()


async def test_a_failed_start_write_never_costs_the_tick(pool, monkeypatch) -> None:  # noqa: ANN001
    """Bookkeeping may not fail a sweep — the RCA must still run."""

    async def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pool, "execute", _boom)
    await record_diagnosis_started(
        pool, signature=_SIG, incident_id="incident-a", now=_NOW,
    )  # must not raise


def test_the_start_is_recorded_BEFORE_the_analysis_runs() -> None:
    """Structural, and the whole item in one assertion: recording it afterwards
    is precisely the bug — a process replaced during those sixteen minutes
    leaves nothing behind."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    source = inspect.getsource(incident_escalation.IncidentEscalationHandler.execute)
    assert "record_diagnosis_started(" in source, (
        "the start marker is gone — an interrupted analysis is invisible again"
    )
    assert source.index("record_diagnosis_started(") < source.index(
        "await self._resolve_incident("
    ), "the marker must be written BEFORE the analysis, or it cannot survive it"
