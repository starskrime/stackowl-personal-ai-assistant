"""How the agents interact — the edges, as a SET, from the stores that hold them.

A05.7's gap said delegation, parliament and owl-to-owl messages "happen inside
mailboxes nobody can watch, so multi-agent behaviour can only be reconstructed
from logs after the fact". MEASURED 2026-09-12, three of its four clauses are
wrong, and the one that holds is the one it states least clearly.

**THE EDGES ARE RECORDED. IN THREE PLACES. NONE OF WHICH ANY SURFACE READS.**

| edge | store | rows | newest |
|---|---|---|---|
| decomposition (same owl) | `tasks.parent_task_id`, `trigger_kind='subgoal'` | 52 | 2026-09-11 |
| delegation (cross owl) | `side_effect_ledger` `tool_name='delegate_task'` | 17 | 2026-08-24 |
| the caller's own turn | `task_outcomes.tool_sequence` | 76 | 2026-08-30 |

`tasks` holds NOT ONE cross-owl edge: all 52 parent links are an owl decomposing
its own work (secretary->secretary x46, sysdesign, headhunter), and all six rows
that carry `parent_owl` are self-delegations that FAILED. So a graph built from
`tasks` alone would truthfully report that the agents never interact — on a
platform where two delegations succeeded the previous day.

**AND "ONLY FROM LOGS" IS TRUE ONLY OF THE RECENT ONES.** The retained logs hold
SEVEN successful cross-owl delegations (`delegate_task.execute: exit`,
`status: ok`), the newest 2026-09-11; the durable ledger holds TWO. The three
delegations on 2026-09-11 each logged `delegate_task: non-durable parent — this
delegation is PROCESS-LOCAL and will not survive a restart`, which is exactly why
they left no row: `_resolve_durable_child_scope` requires a parent task id, an
active durable context AND a db pool, and a chat-initiated delegation has no
parent TASK. That is a property of the delegation path, not of this reader, and
it is named in `InteractionGaps` rather than papered over — a surface that
implied "2 delegations, ever" would be as wrong as one that said zero.

`parliament_sessions` is reported as a COUNT with its nature stated, never as a
panel: it has **0 rows and 0 log lines across every retained log**, while being
constructed at boot (`startup/orchestrator.py`) and reachable by command. A table
that renders empty forever is the `committed_facts` shape this tree already paid
for; a count that says "never run" is information.

NO SECOND VOCABULARY. A delegation's outcome is `DelegationStatus`, the Literal
`a2a_delegation.py` already defines — `ok`, `empty`, `timeout`, `child_error`,
`truncated`, `refused`, `cycle`, `target_not_found`, `off_topic`. Nine terms the
engine already uses; inventing a tenth here would be two names for one fact.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log

#: What kind of edge this is. `decomposition` is an owl splitting its OWN work;
#: `delegation` is one owl asking another. Conflating them is how a dashboard
#: reports a busy multi-agent platform that is actually one owl talking to
#: itself — measured, 52 of 52 `tasks` edges are the first kind.
EdgeKind = Literal["decomposition", "delegation"]

#: How much of a goal reaches the surface. USER CONTENT, same rule as
#: `TaskActivity.goal`: enough to recognise an edge, never the whole thing.
_EXCERPT = 120

#: Bounded the way `read_task_activity` is bounded, and for the same reason: a
#: surface must not become a table scan rendered as a web page.
DEFAULT_LIMIT = 200

#: The ledger row that records one delegation. Named here so the reader does not
#: repeat a magic string its meaning depends on.
_DELEGATE_TOOL = "delegate_task"

#: What to call a delegation whose row carries no `status`. `empty_output` is the
#: documented timeout shape (`A2ADelegator.delegate` returns `""`); `unreadable`
#: is this reader failing, and the two must never share a word.
_OUTCOME_WHEN_ABSENT = {"ok": "unknown", "empty_output": "timeout", "unreadable": "unreadable"}


@dataclass(frozen=True)
class InteractionEdge:
    """One edge between agents, with its outcome and where to go and look.

    `ref` is the row a reader can open next — the child task id for a
    decomposition, the calling task id for a delegation. A graph with no way
    back to the record is a picture rather than an instrument.
    """

    kind: EdgeKind
    from_owl: str | None
    to_owl: str | None
    outcome: str
    at: str
    ref: str
    detail: str


@dataclass(frozen=True)
class InteractionGaps:
    """What this read could NOT see — so a surface can state its denominator.

    Every field here was a real wrong answer before it was a field.
    `delegations_without_a_target` is nine of seventeen, and three of those nine
    are an EMPTY `output` string, which is the shape `A2ADelegator.delegate`
    returns on a timeout — so they are the platform correctly recording that a
    delegation timed out, not corrupt rows. A first pass at this reader called
    them "malformed JSON" and was one edit from putting a data-quality defect
    into the record.

    `delegations_without_a_caller` is FOURTEEN of seventeen, and it is not a join
    bug: the calling task row is simply gone — counting the ledger's `task_id`
    against the task table returns zero for every one of them. The ledger
    outlives the task it describes, so half of each old edge is unrecoverable and
    the count says so rather than the panel drawing an arrow from nowhere.

    (That sentence deliberately does NOT spell the query out. An earlier draft
    quoted it verbatim and `test_no_owner_scope_bypass` flagged this module: the
    detector extracts every string literal and cannot tell a docstring EXPLAINING
    a measurement from a statement the module RUNS. Seventh instance of a guard
    matching prose in this repo's record, and the cure is the same each time —
    describe the query, do not write one.)
    """

    unseen_other_owner: int
    truncated: bool
    delegations_without_a_target: int
    delegations_without_a_caller: int
    newest_delegation_recorded: str | None
    parliament_sessions: int


def _excerpt(value: object) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= _EXCERPT else text[: _EXCERPT - 1] + "…"


def _delegation_record(blob: object) -> tuple[dict[str, Any], str]:
    """The `record` a `delegate_task` side-effect row carries, and WHY if absent.

    THE BLOB IS JSON INSIDE JSON: `result_blob` parses to `{"success":…,
    "output": "<a JSON string>"}`, and `output` is the tool's own serialised
    result. `json_extract(json_extract(...))` in SQL raises `malformed JSON` on
    the rows where `output` is `""`, which is why this is parsed in Python —
    an empty output is a TIMEOUT, a fact worth reporting, not a row to drop.

    THE SECOND RETURN VALUE EXISTS BECAUSE THE FIRST DRAFT CONFLATED TWO THINGS.
    It reported `timeout` whenever the record came back empty, which swept a row
    this reader genuinely could not parse into the same bucket as a real timeout
    — four rows where the live data has three. An outcome that cannot tell "the
    delegation timed out" from "I could not read this row" is unfalsifiable, and
    it is exactly the shape this repo keeps paying for. `empty_output` is the
    timeout signature: an `output` key that is present and empty.
    """
    if not isinstance(blob, str) or not blob:
        return {}, "unreadable"
    try:
        outer = json.loads(blob)
    except (ValueError, TypeError):
        return {}, "unreadable"
    if not isinstance(outer, dict) or "output" not in outer:
        return {}, "unreadable"
    out = outer["output"]
    if isinstance(out, dict):
        inner: Any = out
    elif isinstance(out, str) and out:
        try:
            inner = json.loads(out)
        except (ValueError, TypeError):
            return {}, "unreadable"
    elif isinstance(out, str):
        return {}, "empty_output"
    else:
        return {}, "unreadable"
    if not isinstance(inner, dict):
        return {}, "unreadable"
    rec = inner.get("record")
    if not isinstance(rec, dict):
        return {}, "unreadable"
    # `note` is the tool's one-line summary and it sits BESIDE `record`, not
    # inside it — "archivist handled the sub-task". Reading only `record` threw
    # away the one human-readable field on the edge; the panel would have shown a
    # blank What column for every delegation. Caught by the test, not by reading.
    return {**rec, "note": inner.get("note")}, "ok"


async def read_agent_interactions(
    db: DbPool,
    *,
    owner_id: str,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[InteractionEdge], InteractionGaps]:
    """Every recorded edge between agents for *owner_id*, newest first.

    TWO STATEMENTS, EXPLICIT COLUMNS, OWNER-SCOPED IN EACH. Both `tasks` and
    `side_effect_ledger` are owner-governed (measured against
    `_OWNER_GOVERNED_TABLES`, not assumed), and both statements are written out
    in full rather than built from a table name — `test_no_owner_scope_bypass`
    walks `ast.Constant` literals, so a relation arriving as an f-string
    interpolation is invisible to it. A05.5 shipped that lesson; this obeys it.
    """
    t0 = time.monotonic()
    log.tasks.info(
        "[tasks] interactions.read: entry",
        extra={"_fields": {"owner_id": owner_id, "limit": limit}},
    )

    # DECOMPOSITION. The parent's owl is joined rather than read off
    # `parent_owl`, because `parent_owl` is written ONLY by the delegation path
    # and is therefore correctly NULL for a sub-goal. Reading it here would have
    # reported 46 of 52 edges as having no author — a wrong answer this reader
    # produced once before the join was added.
    decomp = await db.fetch_all(
        "SELECT c.task_id, c.owl_name AS child_owl, c.status, c.created_at, "
        "c.goal, c.trigger_kind, p.owl_name AS parent_owl_actual "
        "FROM tasks c LEFT JOIN tasks p "
        "  ON p.task_id = c.parent_task_id AND p.owner_id = c.owner_id "
        "WHERE c.owner_id = ? AND c.parent_task_id IS NOT NULL "
        "  AND c.parent_task_id != '' "
        "ORDER BY c.created_at DESC LIMIT ?",
        (owner_id, int(limit) + 1),
    )
    truncated = len(decomp) > limit
    decomp = decomp[:limit]

    deleg = await db.fetch_all(
        "SELECT s.task_id, s.status, s.created_at, s.result_blob, "
        "p.owl_name AS from_owl "
        "FROM side_effect_ledger s LEFT JOIN tasks p "
        "  ON p.task_id = s.task_id AND p.owner_id = s.owner_id "
        "WHERE s.owner_id = ? AND s.tool_name = ? "
        "ORDER BY s.created_at DESC LIMIT ?",
        (owner_id, _DELEGATE_TOOL, int(limit)),
    )

    unseen_rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM tasks WHERE owner_id <> ? "
        "AND parent_task_id IS NOT NULL AND parent_task_id != ''",
        (owner_id,),
    )
    unseen = int(unseen_rows[0]["n"]) if unseen_rows else 0

    edges: list[InteractionEdge] = []
    for r in decomp:
        child = str(r["child_owl"]) if r["child_owl"] else None
        edges.append(
            InteractionEdge(
                kind="decomposition",
                from_owl=(str(r["parent_owl_actual"]) if r["parent_owl_actual"] else None),
                to_owl=child,
                outcome=str(r["status"]),
                at=str(r["created_at"]),
                ref=str(r["task_id"]),
                detail=_excerpt(r["goal"]),
            )
        )

    without_target = 0
    without_caller = 0
    newest_delegation: str | None = None
    for r in deleg:
        rec, why = _delegation_record(r["result_blob"])
        to_owl = rec.get("to_owl")
        if not to_owl:
            without_target += 1
        if not r["from_owl"]:
            without_caller += 1
        at = str(r["created_at"])
        if newest_delegation is None or at > newest_delegation:
            newest_delegation = at
        edges.append(
            InteractionEdge(
                kind="delegation",
                from_owl=(str(r["from_owl"]) if r["from_owl"] else None),
                to_owl=(str(to_owl) if to_owl else None),
                # An EMPTY output is a timeout, which `A2ADelegator.delegate`
                # returns "" for — so it is reported as such rather than as a
                # missing record. `unknown` is reserved for a row this reader
                # genuinely cannot read.
                outcome=str(rec.get("status") or _OUTCOME_WHEN_ABSENT[why]),
                at=at,
                ref=str(r["task_id"]),
                detail=_excerpt(rec.get("note")),
            )
        )

    edges.sort(key=lambda e: e.at, reverse=True)

    parliament_rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM parliament_sessions WHERE owner_id = ?",
        (owner_id,),
    )
    parliament = int(parliament_rows[0]["n"]) if parliament_rows else 0

    gaps = InteractionGaps(
        unseen_other_owner=unseen,
        truncated=truncated,
        delegations_without_a_target=without_target,
        delegations_without_a_caller=without_caller,
        newest_delegation_recorded=newest_delegation,
        parliament_sessions=parliament,
    )
    cross = sum(1 for e in edges if e.kind == "delegation")
    log.tasks.info(
        "[tasks] interactions.read: exit — served",
        extra={"_fields": {
            "owner_id": owner_id,
            "edges": len(edges),
            "delegation_edges": cross,
            "decomposition_edges": len(edges) - cross,
            "delegations_without_a_target": without_target,
            "delegations_without_a_caller": without_caller,
            "parliament_sessions": parliament,
            "unseen_other_owner": unseen,
            "duration_ms": (time.monotonic() - t0) * 1000,
        }},
    )
    return edges, gaps
