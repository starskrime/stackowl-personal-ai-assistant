"""What the platform remembers, as a SET — five stores, and which is which.

A05.5's route needs four different questions answered about memory, and none of
them belongs in `control_plane/server.py`:
`test_the_server_module_issues_no_sql_of_its_own` refuses any SQL there, on the
rule that a route must READ THROUGH THE COMPONENT THAT OWNS THE TABLE. It refused
the first version of this code, correctly — `owls/activity.py` and
`pipeline/durable/activity.py` are the same shape for the same reason.

**THE HONEST ANSWER IS NOT ONE NUMBER, and that is the whole design.** MEASURED
2026-09-12:

| store | rows | what it actually is |
|---|---|---|
| curated `~/.stackowl/memory/*.md` | 19 files, 64 entries | what the platform was TOLD to remember |
| `lessons` | 5,964 | what it learned by working |
| `learning_artifacts` | 700 | the raw material behind those |
| `staged_facts` | 237 | SHORT-TERM CONVERSATION HISTORY |
| `committed_facts` | 0 | RETIRED by migration 0112 |

`staged_facts` is the trap. `pipeline/state.py` calls those rows "ONLY short-term
history … the mirror that lets the agent know what it already told him", so
rendering 237 of them under "what the platform remembers about you" presents a
TRANSCRIPT AS KNOWLEDGE. They are counted with their kind stated, never merged
into a total.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stackowl.infra.observability import log


@dataclass(frozen=True)
class StoreCount:
    """One memory store, counted and labelled by WHAT IT IS.

    Typed rather than a dict so the route's JSON keys are written in its handler,
    where the control plane's field-bijection guard can see them and where a
    reader of the surface looks for the contract. MEASURED off-tree before this
    landed: a store returning wire-shaped dicts made EIGHT fields invisible to
    that guard.
    """

    store: str
    kind: str
    rows: int | None
    note: str


@dataclass(frozen=True)
class CuratedTarget:
    """One curated note file and its entries, keyed by who it is about."""

    target: str
    entries: tuple[Any, ...]


#: The other memory stores, each with the SQL that counts it WRITTEN OUT.
#:
#: A loop over table names building `f"... FROM {table}"` is the obvious shape and
#: it is disqualified: all three tables are owner-governed, and
#: `tests/tenancy/test_no_owner_scope_bypass.py` finds a bypass by walking
#: `ast.Constant` string literals — so a relation name that arrives as an
#: INTERPOLATION is invisible to it. MEASURED 2026-09-12 across `src/`: 21
#: f-string statements name their relation dynamically, and this would have been
#: the 22nd, added by the same change that deletes two of that guard's allowlist
#: entries. The guard could not have refused it; the live data could not have
#: caught it either, because every row in all three tables belongs to
#: `principal-default`, so a scoped and an unscoped count are the same number
#: here. Spelling the statements out is what makes the rule able to see them.
_OTHER_MEMORY_STORES: tuple[tuple[str, str, str, str], ...] = (
    (
        "SELECT COUNT(*) AS n FROM staged_facts WHERE owner_id = ?",
        "staged_facts",
        "short-term history",
        "the mirror of what the agent already said; not knowledge about you",
    ),
    (
        "SELECT COUNT(*) AS n FROM committed_facts WHERE owner_id = ?",
        "committed_facts",
        "retired",
        "0 rows since migration 0112; its promotion path is dead on both ends",
    ),
    (
        "SELECT COUNT(*) AS n FROM learning_artifacts WHERE owner_id = ?",
        "learning_artifacts",
        "learning artifacts",
        "raw material behind lessons",
    ),
)


async def read_other_memory_counts(db: Any, *, owner_id: str) -> list[StoreCount]:
    """The other stores, COUNTED and LABELLED by what they actually are.

    Each row carries a `kind` because the honest answer to "what do you remember"
    is not one number. `staged_facts` is short-term conversation history, not
    knowledge (`pipeline/state.py`), and `committed_facts` is retired — reporting
    either as "memories" is the misrepresentation this route exists to avoid.

    Owner-scoped IN the statement, like every other read on this door. `lessons`
    is the one memory store that carries no `owner_id` column at all — measured
    against `_OWNER_GOVERNED_TABLES` rather than assumed — which is why it is
    read without one and these three are not.
    """
    out: list[StoreCount] = []
    for sql, table, kind, note in _OTHER_MEMORY_STORES:
        try:
            rows = await db.fetch_all(sql, (owner_id,))
            out.append(StoreCount(table, kind, int(rows[0]["n"]), note))
        except Exception as exc:  # noqa: BLE001 — B5, a missing table is not fatal
            log.memory.warning(
                "[memory] activity.read: a memory store could not be counted",
                exc_info=exc,
                extra={"_fields": {"store": table}},
            )
            out.append(StoreCount(table, kind, None, note))
    return out


def read_curated_entries() -> list[CuratedTarget]:
    """The curated notes, per target, READ THROUGH THE SAME OBJECT `/memory` uses.

    `CuratedMemory` is the one reader of these files; a second parser here would
    be two answers to "what is remembered", and the two would drift the first
    time the file format changed. `entries()` treats an absent file as an empty
    list rather than an error, which is why a missing owl note costs a row and
    not the response.

    MEASURED 2026-09-12: 19 files, 64 entries, 22,321 chars — `USER.md` holds 7
    and the other 18 are per-owl notes. That asymmetry is why the target is
    carried on every row instead of merging them: "what you remember about ME"
    and "what you remember about working as scout" are different questions.
    """
    from stackowl.memory.curated import USER_TARGET, CuratedMemory, memory_dir

    mem = CuratedMemory()
    targets = [USER_TARGET]
    try:
        targets += sorted(
            p.stem for p in memory_dir().glob("*.md") if p.name != "USER.md"
        )
    except OSError as exc:  # B5 — a listing failure costs the owl half only
        log.memory.warning(
            "[memory] activity.read: could not list curated notes", exc_info=exc
        )
    out: list[CuratedTarget] = []
    for target in targets:
        try:
            entries = mem.entries(target)
        except Exception as exc:  # noqa: BLE001 — B5, one bad file is not the set
            log.memory.warning(
                "[memory] activity.read: a curated note could not be read",
                exc_info=exc,
                extra={"_fields": {"target": target}},
            )
            continue
        out.append(CuratedTarget(target=target, entries=tuple(entries)))
    return out
