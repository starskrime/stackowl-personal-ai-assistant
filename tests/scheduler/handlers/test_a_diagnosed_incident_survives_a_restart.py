"""The platform forgot every diagnosis it had ever made, on every restart.

MEASURED 2026-08-31, and it is the largest single line item on the whole platform::

    incident RCA lanes today          15,724,829 input tokens   72.3% of ALL spend
    distinct RCA sessions                    126   mean 125,181 tokens each
    RCA completions                          100
      verified TRUE                           12
      verified FALSE                          86
    distinct incident signatures produced      2

And the detector says why, on every tick, all day::

    21:42  new_incidents 7  rca_bound 7  running_rca 1  deferred_to_next_tick 6
    21:56  new_incidents 8  rca_bound 8  running_rca 1  deferred_to_next_tick 7
    22:11  new_incidents 7  rca_bound 7  running_rca 1  deferred_to_next_tick 6
    22:31  new_incidents 8  rca_bound 8  running_rca 1  deferred_to_next_tick 7

The same seven or eight incidents are "new" every time.

THE SUPPRESSION EXISTS AND IS CORRECT — AND IT LIVES IN RAM. ``_open_incidents``
is an instance dict, ``self._open_incidents: dict[str, str] = {}``, and so is
``_verdict_failures``, the ceiling that stops a no-verdict RCA retrying for ever.
Both are perfect within one process and both are EMPTY the moment the process is
replaced — and this platform replaces its core on every commit (CodeWatcher
exec-replaces it) as well as on every operator restart. With one RCA per tick and
seven signatures waiting, the dedup cannot even converge before the next restart
wipes it.

So the loop was: restart, forget, re-diagnose the same seven incidents, reach
"verified: False" 86 times out of 100, forget again.

THE DURABLE STORE ALREADY EXISTS AND IS ALREADY USED FOR EXACTLY THIS.
``find_recurring_gaps`` reads ``capability.denied`` / ``capability.escalated`` from
``audit_log`` for the same purpose, and its own comment states the rule: "A pair
already carrying `capability.escalated` is skipped, so a gap that fires every run
alerts once rather than every sweep." This is that pattern, applied to the loop
that costs 72% of the platform's tokens. No new table, no migration, no second
engine.

THE WINDOW IS ARITHMETIC, NOT TASTE. A diagnosis suppresses its signature for
``_DIAGNOSIS_GOOD_FOR_HOURS``. At 24 hours and roughly eight live signatures that is
at most eight RCAs a day — about 1M tokens against today's 15.7M — while a problem
that is still there tomorrow is looked at again, because a stale diagnosis of a
changed system is worth less than a fresh one.
"""

from __future__ import annotations

import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.scheduler.handlers.incident_escalation import (
    DIAGNOSED_EVENT,
    _DIAGNOSIS_GOOD_FOR_HOURS,
    recently_diagnosed,
    record_diagnosis,
)

pytestmark = pytest.mark.asyncio


async def _diagnose(db: DbPool, sig: str, *, age_hours: float = 0.0) -> None:
    await record_diagnosis(
        db, signature=sig, incident_id="incident-abc",
        now=time.time() - age_hours * 3600.0,
    )


async def test_a_diagnosed_signature_is_suppressed(tmp_db: DbPool) -> None:
    """The whole item. Today this cost 15.7M tokens because the answer was in RAM."""
    await _diagnose(tmp_db, "outcome:shell:stop")

    seen = await recently_diagnosed(tmp_db, now=time.time())

    assert "outcome:shell:stop" in seen


async def test_it_SURVIVES_a_new_handler_instance(tmp_db: DbPool) -> None:
    """A restart is the case that matters — CodeWatcher exec-replaces the core on
    every commit, so 'in-memory until the process ends' means 'forgotten hourly'."""
    await _diagnose(tmp_db, "outcome:shell:stop")

    # Nothing is carried over: a fresh read, as a fresh process would do.
    seen = await recently_diagnosed(tmp_db, now=time.time())

    assert seen == {"outcome:shell:stop"}


async def test_an_OLD_diagnosis_stops_suppressing(tmp_db: DbPool) -> None:
    """A problem still present tomorrow deserves a fresh look — a stale diagnosis
    of a changed system is worth less than a new one."""
    await _diagnose(tmp_db, "outcome:shell:stop", age_hours=_DIAGNOSIS_GOOD_FOR_HOURS + 1)

    seen = await recently_diagnosed(tmp_db, now=time.time())

    assert seen == set()


async def test_an_UNVERIFIED_diagnosis_still_suppresses(tmp_db: DbPool) -> None:
    """86 of today's 100 RCAs concluded verified=False. If only VERIFIED verdicts
    suppressed, the 86 would keep re-running for ever — which is exactly what
    happened."""
    await record_diagnosis(
        tmp_db, signature="outcome:web_fetch:stop", incident_id="i-1",
        verified=False, now=time.time(),
    )

    assert "outcome:web_fetch:stop" in await recently_diagnosed(tmp_db, now=time.time())


async def test_a_DIFFERENT_signature_is_not_suppressed(tmp_db: DbPool) -> None:
    """The expensive direction. Over-suppressing blinds the self-heal loop, which
    is worse than the token bill it was built to cut."""
    await _diagnose(tmp_db, "outcome:shell:stop")

    seen = await recently_diagnosed(tmp_db, now=time.time())

    assert "outcome:browser_navigate:stop" not in seen


async def test_an_EMPTY_ledger_suppresses_NOTHING(tmp_db: DbPool) -> None:
    """A fresh install must diagnose normally, not sit silent."""
    assert await recently_diagnosed(tmp_db, now=time.time()) == set()


async def test_a_BROKEN_read_suppresses_nothing(tmp_db: DbPool, monkeypatch) -> None:  # noqa: ANN001
    """Fail toward DIAGNOSING. A suppression that fires because the ledger could
    not be read would silently disable the self-heal loop — the failure mode this
    whole arc exists to prevent."""
    async def _boom(*a: object, **k: object) -> list:
        raise RuntimeError("audit_log unreadable")

    monkeypatch.setattr(tmp_db, "fetch_all", _boom)

    assert await recently_diagnosed(tmp_db, now=time.time()) == set()


async def test_a_FAILED_WRITE_never_breaks_the_tick(tmp_db: DbPool, monkeypatch) -> None:  # noqa: ANN001
    """Recording a diagnosis is bookkeeping. It must not be able to fail a sweep."""
    async def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(tmp_db, "execute", _boom)

    await record_diagnosis(tmp_db, signature="s", incident_id="i", now=time.time())


async def test_the_window_is_stated_and_bounded() -> None:
    """Not taste: at 24h and ~8 live signatures this is at most 8 RCAs a day —
    about 1M tokens against today's 15.7M."""
    assert 1 <= _DIAGNOSIS_GOOD_FOR_HOURS <= 72
    assert DIAGNOSED_EVENT.startswith("incident.")


def test_the_handler_actually_ASKS_the_ledger() -> None:
    """A helper with no caller is the defect class this platform keeps paying for.
    Structural, so wiring cannot be removed while the tests above stay green."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    source = inspect.getsource(incident_escalation)
    assert "await recently_diagnosed(" in source, "the ledger is never read"
    assert source.count("await record_diagnosis(") == 2, (
        "both conclusions must be recorded: a real verdict AND the give-up ceiling "
        "— a restart otherwise hands the same signature three more failed RCAs"
    )


def test_assembly_passes_the_db() -> None:
    """The wiring that makes it real. `db=None` is a byte-identical no-op, which is
    exactly how this could ship looking done and change nothing."""
    import inspect

    from stackowl.scheduler import assembly

    source = inspect.getsource(assembly)
    idx = source.find("IncidentEscalationHandler(")
    assert idx != -1
    assert "db=db," in source[idx : idx + 1800], (
        "the handler was constructed without a database — the dedup stays in RAM"
    )
