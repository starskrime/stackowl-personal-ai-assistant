""""No timestamp column" was false for every store that claimed it.

``store_cadence`` exists because "a store quietly outside the check is exactly
the blind spot this registry exists to remove" — its own words. It then built one
category that puts a store outside the check, ``UNMEASURABLE``, defined as::

    UNMEASURABLE = (None, "no timestamp column; cannot be measured")

MEASURED 2026-09-03 against the live schema. Fifteen stores were declared
UNMEASURABLE. All fifteen have an ``_at`` column::

    job_results              run_at                      newest 2026-09-03
    staged_facts             staged_at                   newest 2026-09-03
    channel_liveness         last_receive_at             newest 2026-09-03
    callback_log             processed_at                newest 2026-09-03
    contradiction_scan_state last_contradiction_scan_at  newest 2026-08-10
    dna_checkpoints          created_at                  newest 2026-07-14
    command_sequence_last    updated_at                  newest 2026-05-23
    ... and eight more with a clock and zero rows

So the hatch was not "these cannot be measured". It was "these are not measured",
on a premise nobody checked against the schema.

WHAT WAS HIDING IN IT. Three of the fifteen have ZERO references anywhere in
``src/`` — ``reindex_queue``, ``langgraph_checkpoints`` and
``contradiction_scan_state``. That is the same measure by which ``job_queue`` was
deleted, and it is the exact class of finding this module was BUILT to produce:
its docstring reports that its first run found ``dreamworker_runs`` and
``kuzu_sync_log`` with zero references, "two tables outliving the features that
wrote them by weeks". ``contradiction_scan_state`` even carries the evidence —
its watermark reads 2026-08-10T19:15:12, the timestamp of the last
``memory.contradiction`` row in the audit log, frozen when migration 0112
retired the fact store its scanner read.

THE CAUSE IS ONE HALF OF A RULE MADE EXECUTABLE AND THE OTHER HALF REMEMBERED.
``test_every_store_declares_its_cadence`` already fails when the schema holds a
table the registry does not name — the module says out loud that "a
hand-maintained list rots the first time someone adds a table and forgets", and
made THAT half a test. Nothing checked that a declaration is TRUE. So the
completeness half could not rot and the correctness half rotted silently.

WHAT CHANGES. ``UNMEASURABLE`` now has to earn itself: a table declaring it must
genuinely have no ``_at`` column, asserted against the live schema. A store whose
WRITER was removed gets ``RETIRED``, which names the state instead of pretending
the store cannot be read — and four stores move out of the hatch into real
measurement, so ``staged_facts`` and ``channel_liveness`` can now alarm within a
day if their loops stop, which they could not do before.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stackowl.health.store_cadence import DECLARATIONS, Cadence

#: The three with zero references anywhere in src/ — measured, not assumed.
ORPHANED = ("reindex_queue", "langgraph_checkpoints", "contradiction_scan_state")

#: Moved out of the hatch into real measurement by this change.
NOW_MEASURED = ("job_results", "staged_facts", "channel_liveness", "callback_log")


def _schema_sql() -> str:
    root = Path(__file__).resolve().parents[2] / "src" / "stackowl" / "db" / "migrations"
    return "\n".join(p.read_text(errors="ignore") for p in sorted(root.glob("*.sql")))


def _columns_for(table: str, sql: str) -> set[str]:
    """Every column name the migrations ever give ``table`` (CREATE + ALTER)."""
    cols: set[str] = set()
    for m in re.finditer(
        rf"CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\s*\);", sql, re.S | re.I,
    ):
        cols |= set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s", m.group(1), re.M | re.I))
    cols |= set(re.findall(
        rf"ALTER TABLE {table} ADD COLUMN\s+([a-z_][a-z0-9_]*)", sql, re.I,
    ))
    return {c.lower() for c in cols}


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_unmeasurable_means_what_it_says() -> None:
    """THE ROOT CAUSE, made executable. The completeness half of this registry's
    rule was already a test; the correctness half was a comment, and it was false
    for all fifteen entries that used it. A store declaring "no timestamp column"
    must genuinely have none."""
    sql = _schema_sql()
    liars = []
    for decl in DECLARATIONS:
        if decl.cadence is not Cadence.UNMEASURABLE:
            continue
        clocks = sorted(c for c in _columns_for(decl.table, sql) if c.endswith("_at"))
        if clocks:
            liars.append(f"{decl.table} declares no clock but has {clocks}")
    assert not liars, (
        "a store is exempt from the silence check on a premise the schema "
        "contradicts:\n  " + "\n  ".join(liars)
    )


def test_a_store_whose_writer_was_removed_says_so() -> None:
    """RETIRED names the state. Filing a writer-less store as "cannot be
    measured" is not merely inaccurate — it is the sentence that kept three
    zero-reference tables out of the one check built to find them."""
    assert Cadence.RETIRED.max_silence_days is None, (
        "a retired store must never alarm — its silence is the design"
    )
    assert "writer" in Cadence.RETIRED.why.lower()


@pytest.mark.parametrize("table", ORPHANED)
def test_the_orphaned_stores_are_declared_retired(table: str) -> None:
    """Measured: zero references anywhere in src/ — the same standard by which
    job_queue was deleted."""
    decl = next(d for d in DECLARATIONS if d.table == table)
    assert decl.cadence is Cadence.RETIRED, f"{table} is {decl.cadence}"
    assert decl.note, f"{table} must say WHY it is retired"


@pytest.mark.parametrize("table", NOW_MEASURED)
def test_the_live_stores_are_actually_measured_now(table: str) -> None:
    """The point of the change, not a side effect. These four are written daily
    and were exempt; now a stopped loop shows up as silence."""
    decl = next(d for d in DECLARATIONS if d.table == table)
    assert decl.clock, f"{table} still declares no clock"
    assert decl.cadence is not Cadence.UNMEASURABLE


def test_every_declaration_with_a_limit_has_a_clock_to_measure() -> None:
    """A cadence that alarms needs something to read. A limit with no clock is a
    check that can never fire — the silent-pass shape this registry is about."""
    for decl in DECLARATIONS:
        if decl.cadence.max_silence_days is not None:
            assert decl.clock, f"{decl.table} declares a limit but no clock column"


def test_no_declaration_names_a_clock_the_schema_does_not_have() -> None:
    """The other direction of the same guard: a declared clock that does not
    exist makes ``silent_stores`` log 'could not read a store's clock' and skip —
    an instrument failure that reads exactly like a healthy store."""
    sql = _schema_sql()
    wrong = []
    for decl in DECLARATIONS:
        if not decl.clock:
            continue
        cols = _columns_for(decl.table, sql)
        if cols and decl.clock.lower() not in cols:
            wrong.append(f"{decl.table}.{decl.clock} is not in the schema")
    assert not wrong, "\n  ".join(wrong)


# --------------------------------------------------------------------------- #
# The verdict has to be readable, or the registry cannot be checked at all      #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_healthy_cadence_verdict_is_visible_in_production() -> None:
    """FOUND WHILE VALIDATING THIS ITEM, and it blocked the validation.

    The contributor logs the SILENT case at INFO with the comment "production
    runs at INFO", and the healthy case at ``log.debug``. So the check runs every
    five minutes and, when all is well, says nothing at all — the closing query
    for this very change returned zero lines against a sweep that had just run.

    The healthy message is the one that carries the denominator, and its own
    comment argues why that matters: "'No store is silent' is worthless without
    'out of how many': this check's own first live run returned a clean zero
    while a date-format bug had skipped almost every store it claimed to cover."
    That argument was won and then emitted where nobody can read it. A healthy
    report at DEBUG is indistinguishable from a check that never ran.
    """
    import logging

    from stackowl.health.contributors import StoreCadenceContributor

    class _Db:
        async def fetch_all(self, _sql: str, *_a: object) -> list[dict]:
            return [{"t": None}]  # every store empty -> nothing silent

    import _pytest.logging  # noqa: F401  (caplog fixture is function-scoped)
    records: list[logging.LogRecord] = []

    class _Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("stackowl")
    handler = _Catch()
    logger.addHandler(handler)
    prior = logger.level
    logger.setLevel(logging.INFO)
    try:
        status = await StoreCadenceContributor(_Db()).health_check()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prior)

    assert status.status == "ok"
    healthy = [r for r in records if "store_cadence" in r.getMessage()]
    assert healthy, (
        "the healthy cadence verdict is invisible at INFO — a clean run and a "
        "check that never ran look identical in production"
    )


# --------------------------------------------------------------------------- #
# The sibling escape: a cadence that never alarms, on a store that is not quiet #
# --------------------------------------------------------------------------- #

#: Declared ON_DEMAND — "only a person's action writes this" — and written by the
#: turn pipeline. MEASURED 2026-09-03: 25 of its 1,192 rows were written after
#: 10:00 UTC that day, every one on an ``incident-*`` session, with no human input
#: since 06:18. Its writer is ``pipeline/steps/assemble.py``.
MISDECLARED_AS_PERSON_DRIVEN = "session_prompts"


def test_a_store_the_loop_writes_is_not_declared_person_driven() -> None:
    """THE SIBLING OF THE HATCH, and it escapes the guard above because it makes
    no claim about the SCHEMA.

    ``ON_DEMAND`` says "Writes only when a PERSON acts. Silence is the operator's
    choice and can never be a defect", and carries no silence limit at all. So a
    store filed there is exempt from the check just as completely as one filed
    UNMEASURABLE — without ever asserting anything a schema could contradict.

    ``session_prompts`` is written by ``assemble`` on every turn that has a
    conversation_id, including machine lanes during a total provider outage. If
    the prompt cache stopped being written the registry would call that silence
    correct, by declaration."""
    decl = next(d for d in DECLARATIONS if d.table == MISDECLARED_AS_PERSON_DRIVEN)
    assert decl.cadence is not Cadence.ON_DEMAND, (
        "a store the turn pipeline writes is declared person-driven, which makes "
        "its silence 'never a defect' — the exemption this registry exists to "
        "remove, reached through a different door"
    )
    assert decl.cadence is Cadence.HOT
    assert decl.clock == "built_at"


@pytest.mark.tripwire
def test_every_store_with_a_clock_declares_it() -> None:
    """THE STRUCTURAL HALF, made executable like the one above it.

    A declaration may legitimately not ALARM — ON_DEMAND and SEED never do. What
    it may not do is discard the one fact that could contradict it. With
    ``clock=None`` nothing reads the store's timestamp, so a wrong cadence can
    never be seen to be wrong: that is exactly how ``session_prompts`` sat filed
    as person-driven while the loop wrote it every turn.

    Declaring the clock does not make a non-alarming store alarm. It makes the
    declaration falsifiable, which is the property the whole registry rests on."""
    sql = _schema_sql()
    # A TABLE THE PARSER CANNOT SEE MUST NOT READ AS A PASS. `schema_migrations`
    # is created by the migration RUNNER, not by a migration file, so it has no
    # CREATE TABLE in this corpus. Found by mutating its declaration to drop the
    # clock and watching the guard stay green — the vacuity this suite exists to
    # refuse, in the suite itself. It is named here with its reason rather than
    # skipped silently, and any OTHER invisible table fails below.
    RUNNER_CREATED = {"schema_migrations"}
    invisible = [
        d.table for d in DECLARATIONS
        if not _columns_for(d.table, sql) and d.table not in RUNNER_CREATED
    ]
    assert not invisible, (
        "this guard cannot resolve the columns of a declared store, so it would "
        f"pass it whatever the declaration says: {invisible}"
    )
    missing = []
    for decl in DECLARATIONS:
        if decl.clock or decl.table in RUNNER_CREATED:
            continue
        clocks = sorted(c for c in _columns_for(decl.table, sql) if c.endswith("_at"))
        if clocks:
            missing.append(f"{decl.table} ({decl.cadence.name}) has {clocks} and declares none")
    assert not missing, (
        "a store keeps a timestamp its declaration throws away, so nothing can "
        "contradict the cadence it claims:\n  " + "\n  ".join(missing)
    )


# --------------------------------------------------------------------------- #
# A cadence is a claim about WHO WRITES, and this one contradicted its neighbour #
# --------------------------------------------------------------------------- #

#: Written only when a PERSON asks for an objective. `objective_tool.py` creates
#: the objective, its subgoals and its "created" event; the driver appends further
#: events only WHILE working one, so every write is downstream of that request.
PERSON_DRIVEN_OBJECTIVE_TABLES = ("objectives", "objective_subgoals", "objective_events")


def test_the_objective_tables_are_not_declared_scheduled() -> None:
    """THE DEFECT, and it contradicted its own neighbour two lines below.

    All three objective tables were declared PERIODIC — "a scheduled job writes
    this", which alarms after seven days of silence. Their only writers are
    reached from ``tools/scheduling/objective_tool.py``: a person asks for an
    objective, and everything else follows from that. ``owls`` sits directly
    beneath them declared ON_DEMAND with the note "11.8 days idle and CORRECT —
    no owl created", which is the identical situation.

    MEASURED 2026-09-04: objectives 0 rows, objective_subgoals 28 (newest
    2026-08-27), objective_events 49 (newest 2026-08-28). The live cadence report
    already flags objective_subgoals SILENT at 7.1d, and objective_events is
    hours behind it — and a silent store makes ``StoreCadenceContributor`` report
    ``degraded``, which reaches him as a CRITICAL operator_health page.

    So the platform was about to page him twice for not having created an
    objective in a week. That is precisely what ON_DEMAND exists to prevent:
    "Silence is the operator's choice and can never be a defect."
    """
    for table in PERSON_DRIVEN_OBJECTIVE_TABLES:
        decl = next(d for d in DECLARATIONS if d.table == table)
        assert decl.cadence is Cadence.ON_DEMAND, (
            f"{table} is declared {decl.cadence.name}, so a week without the "
            "operator creating an objective pages him as a fault"
        )
        assert decl.clock, f"{table} must still declare its clock"


def test_a_person_driven_store_never_alarms() -> None:
    """The property that makes the correction right, asserted on the CADENCE
    rather than on the table list — so a fourth person-driven store added later
    inherits it instead of re-learning it."""
    assert Cadence.ON_DEMAND.max_silence_days is None
    for table in (*PERSON_DRIVEN_OBJECTIVE_TABLES, "owls"):
        decl = next(d for d in DECLARATIONS if d.table == table)
        assert decl.cadence.max_silence_days is None, (
            f"{table} can raise a silence alarm for something only the operator "
            "decides to do"
        )
