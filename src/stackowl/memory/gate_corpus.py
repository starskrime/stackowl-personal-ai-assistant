"""What the platform already remembers — read once, for the remember-gate.

Bakir chose a CROSS-STORE gate on 2026-08-25, over my per-store recommendation,
because "a preference and a fact can say the same thing". This module is the one
place that knows which tables hold memory and how to read them as gate
candidates, so four write sites do not each grow their own copy of that answer.

IT IS BOUNDED, AND THE BOUND IS DELIBERATE. A cross-store check on every write is
four table reads; unbounded, that is a full scan of ~10,000 rows per remembered
fact, which trades a duplication problem for a latency one. So each store
contributes its most recent ``limit`` rows.

THE BOUND IS ALSO A HOLE, AND IT IS LOGGED. A duplicate older than the window
slips through — a fact stated once a year ago and again today is not caught by
rungs 1-2 here. `truncated` names exactly which stores hit the ceiling so that
gap is visible in the log rather than being a silent cap that reads as "checked
everything". If it turns out to matter, the fix is an indexed normalised-key
column, not a bigger number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.remember_gate import Candidate

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool

#: Rows per store per check. Chosen to keep a cross-store write to a few
#: thousand cheap string comparisons rather than a full scan of every memory the
#: platform holds. Not tuned against a latency measurement yet — when it is, this
#: is the number to move.
DEFAULT_LIMIT = 200

#: table -> (id column, text column, store label, has embedding columns)
_SOURCES: tuple[tuple[str, str, str, str, bool], ...] = (
    ("staged_facts", "fact_id", "content", "facts", True),
    ("lessons", "lesson_id", "content", "lessons", True),
    ("reflections", "reflection_id", "summary", "reflections", True),
    ("user_preferences", "pref_id", "value", "preferences", False),
)


@dataclass(frozen=True)
class Corpus:
    candidates: list[Candidate]
    truncated: tuple[str, ...] = ()


async def load_corpus(
    db: DbPool,
    *,
    limit: int = DEFAULT_LIMIT,
    stores: tuple[str, ...] | None = None,
) -> Corpus:
    """Recent rows from every memory store, as gate candidates.

    ``stores`` restricts the read to named labels; None means all of them. A
    store that fails to read is SKIPPED with a warning rather than failing the
    write — the gate is an improvement to memory, never a gate on remembering.
    """
    out: list[Candidate] = []
    truncated: list[str] = []
    for table, id_col, text_col, label, has_embedding in _SOURCES:
        if stores is not None and label not in stores:
            continue
        cols = f"{id_col} AS rid, {text_col} AS txt"
        if has_embedding:
            cols += ", embedding AS emb, embedding_model AS emb_model"
        try:
            rows = await db.fetch_all(
                f"SELECT {cols} FROM {table} ORDER BY rowid DESC LIMIT ?",  # noqa: S608
                (int(limit) + 1,),
            )
        except Exception as exc:  # B5 — a store we cannot read must not cost the write
            log.memory.warning(
                "[gate] corpus: store unreadable — skipping it for this check",
                exc_info=exc, extra={"_fields": {"table": table}},
            )
            continue
        if len(rows) > limit:
            truncated.append(label)
            rows = rows[:limit]
        for r in rows:
            text = r["txt"]
            if not text:
                continue
            out.append(
                Candidate(
                    text=str(text),
                    store=label,
                    row_id=str(r["rid"]),
                    embedding=(r["emb"] if has_embedding else None),
                    embedding_model=(
                        str(r["emb_model"] or "") if has_embedding else ""
                    ),
                )
            )
    return Corpus(candidates=out, truncated=tuple(truncated))
