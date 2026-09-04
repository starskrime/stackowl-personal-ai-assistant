"""Every store declares how often it EXPECTS to be written, and silence alarms.

WHOSE DECISION THIS IS. Bakir asked for "any store with readers but no writes in
N days". Two rules were built to derive that from the data and BOTH were refuted
against the live database:

  * idle > 10x the store's own median gap — alarms for ever on any burst-seeded
    store.
  * wrote on 3+ days in 45, none in 7 — alarms on ``owls`` (correct to be quiet:
    he has not created an owl) AND misses ``dna_checkpoints`` (writer removed on
    purpose).

``owls`` at 11.8 days idle is CORRECT. ``retry_queue`` at 5.9 days is a STOPPED
ENGINE. The data looks identical; only what the store is FOR tells them apart,
and no query can derive purpose. Asked to choose, he took the explicit registry:
each store declares its expected cadence once. That is the whole design — the
missing input is declared rather than guessed.

WHAT MAKES IT SURVIVE. A hand-maintained list rots the first time someone adds a
table and forgets. ``tests/health/test_every_store_declares_its_cadence.py``
fails when the schema holds a table this module does not name, so the registry
cannot silently fall behind the schema — the same move as ``@pytest.mark.tripwire``
and the bytecode guard: make the rule executable rather than remembered.

WHAT IT FOUND ON ITS FIRST RUN, and this is why the class list is worth reading
rather than skimming: ``dreamworker_runs`` (762 rows) and ``kuzu_sync_log``
(20,065 rows) had ZERO references anywhere in ``src/`` — two tables outliving the
features that wrote them by weeks. Declaring a cadence forces the question "who
writes this?" for every store, and for those two the answer was nobody.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from stackowl.infra.observability import log


class Cadence(Enum):
    """How often a store expects to be written, and WHY.

    The reason is part of the value, not a comment: the whole failure this
    registry fixes was a number nobody could justify.
    """

    #: A live loop writes it on ordinary traffic. A day of silence means the loop
    #: has stopped, because the platform does not go a day without a turn.
    HOT = (1.0, "a live loop writes this on ordinary traffic")
    #: A scheduled job or an occasional path writes it. A week of silence outlives
    #: every cadence in the scheduler, so it means the writer is gone.
    PERIODIC = (7.0, "a scheduled job writes this")
    #: Writes only when a PERSON acts. Silence is the operator's choice and can
    #: never be a defect — ``owls`` has been quiet for 11.8 days and is correct.
    ON_DEMAND = (None, "only a person's action writes this")
    #: Written once, at install or by a migration. Silence is the design.
    SEED = (None, "written once at install")
    #: NO CLOCK COLUMN — this store cannot be measured at all. Declared rather
    #: than skipped, because a store quietly outside the check is exactly the
    #: blind spot this registry exists to remove.
    UNMEASURABLE = (None, "no timestamp column; cannot be measured")
    #: The WRITER was removed. Silence is not a fault to chase but a leftover to
    #: delete, and saying so is the point: filing these as "cannot be measured"
    #: is what kept three tables with ZERO references anywhere in `src/` outside
    #: the one check built to find exactly that.
    RETIRED = (None, "the writer was removed; this store should be deleted")

    @property
    def max_silence_days(self) -> float | None:
        days, _ = self.value
        return None if days is None else float(days)

    @property
    def why(self) -> str:
        _, reason = self.value
        return str(reason)


@dataclass(frozen=True)
class StoreDeclaration:
    """One store's declared cadence and the column that dates a write."""

    table: str
    cadence: Cadence
    clock: str | None = None
    #: Overrides the class reason where a store needs its own. Used sparingly:
    #: if every entry needs one, the classes are wrong.
    note: str = ""


def _hot(table: str, clock: str) -> StoreDeclaration:
    return StoreDeclaration(table, Cadence.HOT, clock)


def _periodic(table: str, clock: str, note: str = "") -> StoreDeclaration:
    return StoreDeclaration(table, Cadence.PERIODIC, clock, note)


def _on_demand(table: str, clock: str | None = None, note: str = "") -> StoreDeclaration:
    return StoreDeclaration(table, Cadence.ON_DEMAND, clock, note)


def _seed(table: str, clock: str | None = None) -> StoreDeclaration:
    return StoreDeclaration(table, Cadence.SEED, clock)


def _unmeasurable(table: str, note: str = "") -> StoreDeclaration:
    return StoreDeclaration(table, Cadence.UNMEASURABLE, None, note)


def _retired(table: str, clock: str, note: str) -> StoreDeclaration:
    """A store whose writer is gone. The clock is still declared — the watermark
    is the EVIDENCE of when the writer died, and throwing it away is how this
    went unnoticed for 24 days."""
    return StoreDeclaration(table, Cadence.RETIRED, clock, note)


#: THE REGISTRY. Adding a table to the schema without adding it here fails a test.
DECLARATIONS: tuple[StoreDeclaration, ...] = (
    # --- HOT: a live turn or a frequent loop writes these ---------------------
    _hot("tasks", "created_at"),
    _hot("task_outcomes", "captured_at"),
    _hot("job_runs", "ran_at"),
    _hot("jobs", "created_at"),
    _hot("cost_records", "recorded_at"),
    _hot("messages", "created_at"),
    _hot("message_ledger", "created_at"),
    _hot("conversations", "started_at"),
    _hot("sessions", "created_at"),
    _hot("audit_log", "timestamp"),
    _hot("turn_decisions", "created_at"),
    _hot("side_effect_ledger", "created_at"),
    _hot("reflections", "created_at"),
    _hot("lessons", "created_at"),
    _hot("notification_log", "created_at"),
    _hot("delivery_attempts", "created_at"),
    _hot("approach_rating_pending", "created_at"),

    # --- PERIODIC: a scheduled job or an occasional path ----------------------
    _periodic("learning_artifacts", "created_at"),
    _periodic("skills", "loaded_at"),
    _periodic("skill_audit", "ts"),
    _periodic("skill_ownership", "attached_at"),
    _periodic("tool_heuristics", "created_at"),
    _periodic("undelivered_outbox", "created_at"),
    # retry_queue's declaration stood HERE and is gone with the table (migration
    # 0135, 2026-09-03). It is worth a line of history: this registry declared it
    # PERIODIC/7d on the morning of 09-03 — "a week of total silence means the
    # engine stopped" — which would have paged him on 09-04 about a store whose
    # writer had been removed ON PURPOSE five days earlier. Asking "who writes
    # this?" for every store is what the registry is FOR, and answering it for
    # that row is what led to the table being dropped the same day.
    # PERSON-DRIVEN, not scheduled — the same situation as `owls` directly
    # below, which was already declared correctly. Every write to these three is
    # downstream of a request: `tools/scheduling/objective_tool.py` creates the
    # objective, its subgoals and its "created" event, and the driver appends
    # further events only WHILE working one. With no objective there is nothing
    # to write, and that is the operator's choice.
    #
    # MEASURED 2026-09-04: objectives 0 rows, objective_subgoals 28 (newest
    # 2026-08-27), objective_events 49 (newest 2026-08-28). Declared PERIODIC the
    # live report already flagged objective_subgoals SILENT at 7.1d and
    # objective_events was hours behind — and a silent store makes
    # StoreCadenceContributor report `degraded`, which reaches him as a CRITICAL
    # operator_health page. The platform was about to page him twice for not
    # having created an objective in a week.
    _on_demand("objective_events", "created_at",
               "written while working an objective — none exists unless asked for"),
    _on_demand("objective_subgoals", "created_at",
               "created by the objective tool when the operator asks"),
    _on_demand("objectives", "created_at",
               "created by the objective tool when the operator asks"),

    # --- ON_DEMAND: only a person's action writes these -----------------------
    _on_demand("owls", "created_at", "11.8 days idle and CORRECT — no owl created."),
    _on_demand("owl_dna", "updated_at"),
    _on_demand("owl_dna_authored", "updated_at"),
    _on_demand("owl_profiles", "created_at"),
    _on_demand("user_preferences", "updated_at"),
    # NOT person-driven, measured. `assemble` writes this on every turn that
    # carries a conversation_id: 25 of its 1,192 rows landed after 10:00 UTC on
    # 2026-09-03, every one on an `incident-*` session, with no human input since
    # 06:18. Filed ON_DEMAND it was exempt from the silence check — "silence is
    # the operator's choice and can never be a defect" — so a prompt cache that
    # stopped being written would have read as correct.
    _hot("session_prompts", "built_at"),
    _on_demand("notification_overrides", "created_at"),
    _on_demand("plugins", "installed_at"),
    _on_demand("thread_registry", "created_at"),

    # --- SEED: written once ---------------------------------------------------
    _seed("principals", "created_at"),
    _seed("onboarding_events", "recorded_at"),
    _seed("onboarding", "shown_at"),
    _seed("stackowl_meta", "updated_at"),
    _seed("schema_migrations", "applied_at"),

    # --- RETIRED: the writer is gone; these are leftovers to delete -----------
    # MEASURED 2026-09-03. All four were filed UNMEASURABLE ("no timestamp
    # column") and all four have one. Three have ZERO references anywhere in
    # `src/` — the same standard by which `job_queue` was deleted.
    _retired("reindex_queue", "queued_at",
             "zero references anywhere in src/; 0 rows"),
    _retired("langgraph_checkpoints", "created_at",
             "zero references anywhere in src/; 0 rows"),
    _retired("contradiction_scan_state", "last_contradiction_scan_at",
             "zero references in src/; watermark frozen at 2026-08-10T19:15:12, "
             "the last memory.contradiction row, when migration 0112 retired the "
             "fact store its scanner read"),
    _retired("committed_facts", "committed_at",
             "0 rows since migration 0112; 68 readers remain, no writer"),
    _retired("dna_checkpoints", "created_at",
             "writer removed on purpose; see ESC-91"),

    # --- Moved OUT of the hatch: these are written and can now be measured -----
    _periodic("job_results", "run_at", "one row per scheduled job run"),
    _hot("staged_facts", "staged_at"),
    _hot("channel_liveness", "last_receive_at"),
    _on_demand("callback_log", "processed_at",
               "written when a person presses a channel button"),
    _on_demand("webhook_events_log", "received_at", "written by an inbound webhook"),
    _on_demand("parliament_sessions", "started_at", "written when a session is convened"),
    _on_demand("command_sequence_edges", "updated_at", "written when a person runs commands"),
    _on_demand("command_sequence_last", "updated_at", "written when a person runs commands"),
    _periodic("notification_queue", "created_at", "written by the proactive delivery path"),
    _periodic("cache_breakpoint_probes", "last_confirmed_at", "written by the cache probe"),
)

_BY_TABLE = {d.table: d for d in DECLARATIONS}


@dataclass(frozen=True)
class SilentStore:
    """A store that has gone quiet past its own declaration."""

    table: str
    idle_days: float
    allowed_days: float
    why: str


@dataclass(frozen=True)
class CadenceReport:
    """What one pass looked at, not just what it found.

    THE COUNT IS PART OF THE ANSWER. "No store is silent" is worthless without
    "out of how many" — the first live run of this check returned a clean zero
    while a date-format bug had silently skipped almost every store it claimed to
    cover. A result that carries its own denominator cannot lie that way twice.
    """

    silent: tuple[SilentStore, ...]
    measured: int
    empty: int
    unreadable: int


async def cadence_report(db: object, *, now: float | None = None) -> CadenceReport:
    """One pass, with the denominator attached."""
    stamp = time.time() if now is None else now
    silent = await silent_stores(db, now=stamp)
    measured = empty = unreadable = 0
    for decl in DECLARATIONS:
        if decl.cadence.max_silence_days is None or decl.clock is None:
            continue
        try:
            rows = await db.fetch_all(  # type: ignore[attr-defined]
                f"SELECT MAX({decl.clock}) AS t FROM {decl.table}", (),  # noqa: S608
            )
        except Exception:  # noqa: BLE001 — silent_stores already logged it
            unreadable += 1
            continue
        raw = rows[0]["t"] if rows else None
        if raw is None:
            empty += 1
        elif _as_epoch(raw) is None:
            unreadable += 1
        else:
            measured += 1
    return CadenceReport(
        silent=tuple(silent), measured=measured, empty=empty, unreadable=unreadable,
    )


async def silent_stores(db: object, *, now: float | None = None) -> list[SilentStore]:
    """Every declared store whose silence exceeds what it declared.

    Never raises: a health probe that can crash takes the sweep with it. A store
    whose clock cannot be read is SKIPPED rather than reported, because "I could
    not measure it" and "it has stopped" are different claims and must not arrive
    looking the same — the instrument failure this repo has already paid for.
    """
    stamp = time.time() if now is None else now
    out: list[SilentStore] = []
    for decl in DECLARATIONS:
        limit = decl.cadence.max_silence_days
        if limit is None or decl.clock is None:
            continue
        try:
            rows = await db.fetch_all(  # type: ignore[attr-defined]
                f"SELECT MAX({decl.clock}) AS t FROM {decl.table}", (),  # noqa: S608
            )
        except Exception as exc:  # noqa: BLE001 — one unreadable store may not
            log.scheduler.warning(                       # hide the rest
                "[cadence] could not read a store's clock",
                exc_info=exc, extra={"_fields": {"table": decl.table}},
            )
            continue
        raw = rows[0]["t"] if rows else None
        if raw is None:
            continue  # empty table: a QUESTION, not an answer. Not this check's.
        last = _as_epoch(raw)
        if last is None:
            # LOUD, not `continue`. The first cut swallowed this and returned a
            # clean "no store is silent" while measuring almost nothing — see
            # _as_epoch for what it was swallowing.
            log.scheduler.warning(
                "[cadence] a store's clock is in a format this check cannot read",
                extra={"_fields": {
                    "table": decl.table, "clock": decl.clock,
                    "value": str(raw)[:40],
                }},
            )
            continue
        idle = (stamp - last) / 86400.0
        if idle > limit:
            out.append(SilentStore(
                table=decl.table, idle_days=idle, allowed_days=limit,
                why=decl.note or decl.cadence.why,
            ))
    return out


def _as_epoch(raw: object) -> float | None:
    """Epoch seconds from either storage format this database actually uses.

    TWO FORMATS, AND THE FIRST CUT ONLY HANDLED ONE. ``task_outcomes.captured_at``
    is an epoch float; ``tasks.created_at`` is an ISO-8601 string
    ("2026-09-02T22:51:25.056536+00:00"). A bare ``float(raw)`` raises on the
    second, and the original code caught that and moved on — so the check
    returned "no store is silent" having silently skipped most of the stores it
    claimed to cover. Caught by asking what the zero was MADE OF rather than
    accepting it, which is the rule this repo already has for exactly this.

    Returns None when neither reading works, and the caller logs it.
    """
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except (ValueError, TypeError):
        return None


def declaration_for(table: str) -> StoreDeclaration | None:
    """What *table* declared, or None if it declared nothing."""
    return _BY_TABLE.get(table)
