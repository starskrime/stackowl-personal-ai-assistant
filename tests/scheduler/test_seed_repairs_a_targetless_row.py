"""ESC-42 — a seeded job created without a destination can never be repaired.

THE MEASUREMENT THAT FOUND IT. `morning_brief-15904936` has target_channels NULL
and target_addresses NULL, so `deliver_for_job` attempts ZERO channels and returns
rollup="undeliverable". The operator's daily brief has not reached him in 14 days —
and that brief is what carries the answer to the ESC-40 lesson-injection experiment,
computed daily and delivered never.

THE OBVIOUS READING IS WRONG. It is not a misconfiguration. Read live 2026-08-23:

    settings.brief.channels                  -> ['telegram']
    _resolve_owner_addresses(...)            -> {'telegram': 72055773}

The configuration is CORRECT and has been. The seeder would produce a deliverable
row today if it ran.

THE ROOT CAUSE is that it cannot run. `_seed_daily_schedule` is create-only::

    existing = await db.fetch_all(_SELECT_EXISTING_SQL, (handler_name,))
    if existing:
        log.scheduler.debug("... already present — noop")   # DEBUG: invisible
        return

So a row seeded when no address was resolvable (before the target columns existed,
or before telegram had a resolvable owner) is frozen in that state forever. Fixing
the settings afterwards changes nothing, and the "noop" that decides this is logged
at DEBUG, which production does not emit. That is the first recurring defect shape —
an actuator wired on only some paths — with the create path wired and the repair
path absent.

WHY A REPAIR AND NOT A RE-SEED. The row may carry operator intent: a changed
schedule, a disable, a status. This fills in a MISSING destination and touches
nothing else. It is additive, and it is the narrowest thing that makes the brief
deliverable again.
"""

from __future__ import annotations

import json

import pytest

from stackowl.scheduler.assembly import _repair_missing_target


class _FakeDb:
    """Records what SQL it was asked to run, so the test asserts the EFFECT."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetch_all(self, sql: str, params: tuple[object, ...] = ()) -> list[dict]:
        del sql, params
        return list(self._rows)

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.executed.append((sql, params))


def _row(job_id: str, channels: object, addresses: object) -> dict[str, object]:
    return {
        "job_id": job_id,
        "target_channels": channels,
        "target_addresses": addresses,
    }


# ---------------------------------------------------------------------------
# The repair
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_targetless_row_is_stamped_when_a_destination_now_resolves() -> None:
    db = _FakeDb([_row("morning_brief-15904936", None, None)])

    repaired = await _repair_missing_target(
        db, handler_name="morning_brief",
        target_channels=["telegram"], target_addresses={"telegram": 72055773},
    )

    assert repaired is True
    assert len(db.executed) == 1
    sql, params = db.executed[0]
    assert "UPDATE jobs" in sql
    assert "target_channels" in sql and "target_addresses" in sql
    assert json.loads(str(params[0])) == ["telegram"]
    assert json.loads(str(params[1])) == {"telegram": 72055773}
    assert params[-1] == "morning_brief-15904936"


@pytest.mark.asyncio
async def test_an_empty_list_counts_as_targetless() -> None:
    """The column may hold NULL or '[]' depending on when the row was written."""
    db = _FakeDb([_row("j", "[]", "{}")])
    assert await _repair_missing_target(
        db, handler_name="h", target_channels=["telegram"],
        target_addresses={"telegram": 1},
    ) is True


# ---------------------------------------------------------------------------
# The ways an over-eager repair would be WRONG
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_row_that_ALREADY_has_a_target_is_never_touched() -> None:
    """The operator may have set this deliberately. A repair that overwrites a
    real destination is not a repair."""
    db = _FakeDb([_row("j", '["slack"]', '{"slack":"C123"}')])
    assert await _repair_missing_target(
        db, handler_name="h", target_channels=["telegram"],
        target_addresses={"telegram": 1},
    ) is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_nothing_happens_when_no_destination_resolves() -> None:
    """Stamping an empty target would rewrite the row to the state it is already
    in, and claim a repair that did nothing."""
    db = _FakeDb([_row("j", None, None)])
    assert await _repair_missing_target(
        db, handler_name="h", target_channels=[], target_addresses={},
    ) is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_nothing_happens_when_the_job_does_not_exist() -> None:
    """The create path owns that case; this one must not invent a row."""
    db = _FakeDb([])
    assert await _repair_missing_target(
        db, handler_name="h", target_channels=["telegram"],
        target_addresses={"telegram": 1},
    ) is False
    assert db.executed == []


@pytest.mark.asyncio
async def test_it_touches_ONLY_the_target_columns() -> None:
    """The row may carry operator intent — a changed schedule, a disable, a
    status. Filling in a missing destination must not disturb any of it."""
    db = _FakeDb([_row("j", None, None)])
    await _repair_missing_target(
        db, handler_name="h", target_channels=["telegram"],
        target_addresses={"telegram": 1},
    )
    sql = db.executed[0][0]
    for column in ("schedule", "enabled", "status", "next_run_at", "params"):
        assert column not in sql, f"the repair must not write {column}"


@pytest.mark.asyncio
async def test_a_db_failure_never_raises() -> None:
    """Seeding runs during startup assembly. A repair that throws would take the
    scheduler down over a delivery address."""

    class _Boom(_FakeDb):
        async def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
            raise RuntimeError("db gone")

    db = _Boom([_row("j", None, None)])
    assert await _repair_missing_target(
        db, handler_name="h", target_channels=["telegram"],
        target_addresses={"telegram": 1},
    ) is False
