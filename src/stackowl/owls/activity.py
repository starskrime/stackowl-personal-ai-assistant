"""Per-agent activity — the RUNTIME half the shared card has never carried.

A04.1's gap, measured: `OwlAgentManifest` is the ONE agent descriptor, 23 fields
read by 29 modules, and **every field is static configuration**. The `owls` table
adds nothing runtime either (name, display_name, role, lifecycle, origin,
manifest_json, owner_id, created_at, updated_at). So "what is this agent doing"
has had nowhere to live, and A05.3 and A05.6 both block on it.

**IT IS DERIVED, NEVER DENORMALISED ONTO THE CARD.** `owls`' scalar columns are a
DERIVED INDEX written only by `upsert`, in the same statement as `manifest_json`,
"so an index column can never disagree with the document it indexes"
(`owls/store.py`). A `last_task_at` column there would have a second writer on a
hot path and be erased by the next boot seed — defect shapes 4 and 6 in one.

**TWO STORES, AND THEY ARE NOT INTERCHANGEABLE. This is the whole design.**

* `tasks` answers **NOW**: `status`, `lease_expires_at`, `attempt_count`. MEASURED
  2026-09-11 — of 1,240 rows, **849 carry an `owl_name`**, and that column is a
  query predicate in **ZERO** SQL statements anywhere in `src/`. Written
  everywhere, read nowhere: the write-with-no-reader shape, on precisely the
  column this item needs. (849 is also the `failed` count. Genuine coincidence,
  checked, and noted so the next reader does not take it for a copy-paste slip.)
* `task_outcomes` answers **EVER**: it has **no status column at all** — every row
  is terminal, captured at end-of-pipeline. 19,900 rows cannot say what is
  happening now, at any volume.

So the two facts are returned SEPARATELY NAMED and SEPARATELY SOURCED, and there
is deliberately no fused `status` enum. A single enum spanning a live table and a
history table would go into `/api/v1` JSON and could never be split back out — and
worse, it would let a surface present HISTORY AS LIVENESS.

**A ZERO IN `in_flight` IS OFTEN CORRECT, AND THIS NEARLY WENT IN BACKWARDS.**
Seven of eleven owls have zero `tasks` rows. My first reading called that an
inventory bug; my second called it a missing predicate. Both were wrong.
`enqueue_turn_task` has exactly ONE call site (`startup/orchestrator.py`), and the
comment above it says why: only the gateway owl route reaches `deliver.py`, which
is what COMPLETES the row, so "enqueuing a command or parliament turn would create
a task nothing ever closes". `rca_gatherer`, `hypothesis` and `verifier` are
parliament/RCA stages that run INSIDE another owl's turn. 3,240 outcomes and zero
tasks is the truth, not a gap — and a reader that unioned the two stores to make
all eleven look busy would have manufactured coverage.

**AND THE HOLE IS NAMED RATHER THAN FILLED.** `live_activation` is always None
here. An agent wedged mid-turn inside another owl's turn holds no `tasks` row and
has no durable home at all; that is A04.2's item ("extend the lease to the agent,
never mint a second one"), and filling this field with outcome history would be
the manufactured coverage above, one field down.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest

#: In-flight, TAKEN FROM THE ENGINE RATHER THAN CHOSEN. `('running', 'recovering')`
#: is the durable store's own compare-and-swap predicate — `claim_for_recovery`
#: and the child-liveness read both use exactly this pair — so this reader agrees
#: with the loop that actually holds the work instead of inventing a second
#: definition of "busy". `parked` is deliberately NOT here: the store's other
#: predicate (`NOT IN ('completed','failed','dead_letter','parked')`) counts a
#: parked row as unfinished, which it is, but nothing is working on it.
_LIVE_STATUSES = ("running", "recovering")


@dataclass(frozen=True)
class OwlActivity:
    """One agent's card beside its runtime facts.

    `manifest` is the SHARED object BY REFERENCE, never a copy and never fused
    into a wider model: A05.3 consumes the card half and ignores runtime; A05.6
    grows the runtime half without touching A05.3's contract.
    """

    manifest: OwlAgentManifest
    #: From `tasks`. Authoritative about NOW. A legitimate zero for a stage owl.
    in_flight: int
    pending: int
    #: From `task_outcomes`. HISTORY — epoch seconds of the last completed turn.
    last_seen_at: float | None
    recent_turns: int
    #: A04.2's hole, named. Always None until an agent lease exists.
    live_activation: None = None


@dataclass(frozen=True)
class ActivityGaps:
    """What the join could not attach — counted, because it is not small.

    MEASURED 2026-09-11: 391 task rows carry no `owl_name` and 324 name an owl
    that no longer exists (Brain 110, sysfup 119, headhunter 79, sysdesign 16 —
    all terminal, all last written mid-August). An INNER JOIN would have hidden
    58% of the table behind a number that looks plausible. These are reported so
    a surface built on this reader can say what it could not see.
    """

    unattributed_tasks: int
    orphaned_tasks: int
    orphaned_outcomes: int


async def read_owl_activity(
    db: DbPool,
    manifests: list[OwlAgentManifest],
    *,
    owner_id: str,
    since_epoch: float,
) -> tuple[list[OwlActivity], ActivityGaps]:
    """Every agent's card with its runtime facts, plus what did not attach.

    TWO grouped queries, never one per owl: there is no index on
    `tasks(owl_name)` — the live indexes are owner, status, parent, session,
    claimable and lease-expiry — so a per-owl predicate would be an N+1 over a
    table holding 16.8 MB of `checkpoint_blob`. `task_outcomes` IS indexed for
    exactly this read (`idx_task_outcomes_owner_owl_captured`).

    BOTH SIDES ARE OWNER-SCOPED IN THE STATEMENT. A JOIN scoping one side would
    pass `test_no_owner_scope_bypass`, which clears a statement when `owner_id`
    appears anywhere in it — so the fence cannot see that bug and this shape
    avoids being able to express it.
    """
    t0 = time.monotonic()
    log.startup.info(
        "[owls] activity.read: entry",
        extra={"_fields": {"owls": len(manifests), "since_epoch": since_epoch}},
    )

    known = {m.name for m in manifests}

    task_rows = await db.fetch_all(
        "SELECT owl_name, status, COUNT(*) AS n FROM tasks "
        "WHERE owner_id = ? GROUP BY owl_name, status",
        (owner_id,),
    )
    outcome_rows = await db.fetch_all(
        "SELECT owl_name, COUNT(*) AS n, MAX(captured_at) AS last_at "
        "FROM task_outcomes WHERE owner_id = ? AND captured_at >= ? "
        "GROUP BY owl_name",
        (owner_id, since_epoch),
    )

    live: dict[str, int] = {}
    waiting: dict[str, int] = {}
    unattributed = 0
    orphaned_tasks = 0
    for row in task_rows:
        name = str(row["owl_name"] or "")
        n = int(row["n"])
        if not name:
            unattributed += n
            continue
        if name not in known:
            orphaned_tasks += n
            continue
        if str(row["status"]) in _LIVE_STATUSES:
            live[name] = live.get(name, 0) + n
        elif str(row["status"]) == "pending":
            waiting[name] = waiting.get(name, 0) + n

    turns: dict[str, int] = {}
    last: dict[str, float] = {}
    orphaned_outcomes = 0
    for row in outcome_rows:
        name = str(row["owl_name"] or "")
        n = int(row["n"])
        if not name or name not in known:
            orphaned_outcomes += n
            continue
        turns[name] = n
        raw_last = row["last_at"]
        if raw_last is not None:
            last[name] = float(raw_last)

    out = [
        OwlActivity(
            manifest=m,
            in_flight=live.get(m.name, 0),
            pending=waiting.get(m.name, 0),
            last_seen_at=last.get(m.name),
            recent_turns=turns.get(m.name, 0),
        )
        for m in manifests
    ]
    gaps = ActivityGaps(
        unattributed_tasks=unattributed,
        orphaned_tasks=orphaned_tasks,
        orphaned_outcomes=orphaned_outcomes,
    )

    duration_ms = (time.monotonic() - t0) * 1000
    log.startup.info(
        "[owls] activity.read: exit — served",
        extra={"_fields": {
            "owls": len(out),
            "in_flight_total": sum(a.in_flight for a in out),
            "pending_total": sum(a.pending for a in out),
            "unattributed_tasks": gaps.unattributed_tasks,
            "orphaned_tasks": gaps.orphaned_tasks,
            "orphaned_outcomes": gaps.orphaned_outcomes,
            "duration_ms": duration_ms,
        }},
    )
    return out, gaps
