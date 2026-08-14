"""What is left of the dream-worker helpers: the embedding rebuild and its counters.

WHAT THIS MODULE USED TO BE. Twenty definitions supporting the nightly
consolidation pass — the :class:`DreamWorkerCheckpoint` model, resume/advance/
finalize/mark-failed run bookkeeping, stuck-eligible counting, contradiction
watermarks and boundary ids, the committed-fact scan loader, and the
contradiction audit writer. Every one of them served phases that were fact work
over a store that has held zero rows since D08.1's migration 0112, and the
DreamWorker was stripped to its N01 seat in D08.2 seam 3 pass 1.

WHY ANYTHING SURVIVES. ``reembed_committed_facts`` has a caller that is not the
dream worker: ``cli/app.py`` uses it for the ``db reindex-memory`` command. That
command is user-facing, so removing it is not an autonomous decision — it is
raised as ESC-5, with the observation that it rebuilds a vector table over an
empty store and that D08.1 already removed the equivalent ``/memory reindex``
slash command for exactly that reason.

The two ``count_committed_*`` helpers survive alongside it because
``tests/memory/test_lancedb_dim_swap.py`` uses them to assert the rebuild's
vectorless-legacy healing path — they cover the surviving function, so they are
coverage rather than residue.

So this module is deliberately a stub, and the module
name is deliberately unchanged: if ESC-5 says the CLI command goes, the whole
file goes with it, and renaming something on its way out would only churn the
import in ``cli/app.py``.

The functions are carried over VERBATIM. ``reembed_committed_facts`` uses none
of the nineteen definitions that were removed — only one module-level SQL
constant, which came with it — and that is what made this a clean strip rather
than a rewrite.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.memory.sqlite_helpers import pack_embedding as _pack_embedding

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.db.pool import DbPool


#: The SoT read for the rebuild: SQLite is authoritative, never the LanceDB table
#: that is about to be dropped and recreated.
_SELECT_COMMITTED_FACTS_SQL = """
SELECT fact_id, content, embedding, embedding_model, committed_at,
       source_type, source_ref, tags, trust
FROM committed_facts
"""


async def reembed_committed_facts(
    db: DbPool,
    lancedb: object,
    *,
    embed: Callable[[list[str]], Awaitable[list[list[float]]]],
    active_model: str,
    active_dim: int,
    batch_cap: int | None = None,
) -> int:
    """F066/F062 cure — rebuild the LanceDB corpus at the active model/dim.

    Sources every committed fact from the SQLite ``committed_facts`` SoT (never
    the about-to-drop LanceDB table), re-embeds the content through ``embed``,
    rebuilds the table at ``active_dim`` (drop+recreate via the reindex
    target-dim path), writes the corpus-identity sidecar, and updates each
    fact's ``embedding`` + ``embedding_model`` in SQLite so the SoT and the
    vector store agree. Returns the number of facts re-embedded.

    ``batch_cap`` is available for host-scaling, but the dream-worker caller
    intentionally rebuilds in ONE pass (cap=None): the sidecar is stamped to the
    new identity only after the table is fully populated, so the F062 recall gate
    matches ONLY a complete corpus. A capped (partial) rebuild would have to stamp
    the sidecar while incomplete — recall would then serve a partial corpus as
    "matched/confirmed". Until the (rare, model-swap-triggered) rebuild finishes,
    recall stays honest on FTS via the F062 gate (corpus still mismatched).
    Build-new-then-swap: on any embed failure the old table is left intact and
    recall stays on FTS.
    """
    log.memory.info(
        "[memory] dw_helpers.reembed_committed_facts: entry",
        extra={"_fields": {"active_model": active_model, "active_dim": active_dim, "cap": batch_cap}},
    )
    rows = await db.fetch_all(_SELECT_COMMITTED_FACTS_SQL)
    if batch_cap is not None and batch_cap > 0:
        rows = rows[:batch_cap]
    if not rows:
        log.memory.info("[memory] dw_helpers.reembed_committed_facts: exit — no committed facts")
        return 0

    fact_ids = [row["fact_id"] for row in rows]
    contents = [row["content"] for row in rows]
    try:
        vectors = await embed(contents)
    except Exception as exc:
        # B5 — re-embedding failed; leave the old corpus intact, stay on FTS.
        log.memory.error(
            "[memory] dw_helpers.reembed_committed_facts: embed failed — corpus unchanged",
            exc_info=exc,
            extra={"_fields": {"count": len(contents)}},
        )
        return 0
    if len(vectors) != len(rows) or any(len(v) != active_dim for v in vectors):
        log.memory.error(
            "[memory] dw_helpers.reembed_committed_facts: embed shape mismatch — corpus unchanged",
            extra={
                "_fields": {
                    "expected_n": len(rows),
                    "got_n": len(vectors),
                    "active_dim": active_dim,
                }
            },
        )
        return 0

    records: list[tuple[str, list[float], dict[str, object]]] = [
        (
            row["fact_id"],
            vectors[i],
            {
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "content": row["content"],
                "trust": row["trust"],
                "embedding_model": active_model,
            },
        )
        for i, row in enumerate(rows)
    ]
    # Rebuild the LanceDB table at the new dim (drop+recreate+fill), then stamp
    # the sidecar so the F062 recall gate now MATCHES and semantic resumes.
    written = await lancedb.reindex(records, target_dim=active_dim)  # type: ignore[attr-defined]
    await lancedb.set_corpus_identity(active_model, active_dim)  # type: ignore[attr-defined]

    # Keep the SQLite SoT consistent: update each fact's embedding + model so a
    # later contradiction scan / FTS row carries the new identity.
    for i, fact_id in enumerate(fact_ids):
        blob = _pack_embedding(vectors[i])
        await db.execute(
            "UPDATE committed_facts SET embedding = ?, embedding_model = ? WHERE fact_id = ?",
            (blob, active_model, fact_id),
        )
    log.memory.info(
        "[memory] dw_helpers.reembed_committed_facts: exit",
        extra={"_fields": {"written": written, "active_model": active_model}},
    )
    return int(written)


async def count_committed_with_vectors(db: DbPool) -> int:
    """Count committed facts that carry a non-empty embedding blob.

    Used by the drift-cure phase to decide whether there is anything to
    re-embed: a corpus with zero vectored facts has nothing to rebuild. The
    ``committed_facts`` access lives here (with the rest of the dual-bridge SQL)
    rather than inline in the handler so the owner-scope register stays in one
    allowlisted place.
    """
    rows = await db.fetch_all(
        "SELECT COUNT(*) AS n FROM committed_facts "
        "WHERE embedding IS NOT NULL AND LENGTH(embedding) > 0"
    )
    count = int(rows[0]["n"]) if rows else 0
    log.memory.debug(
        "[memory] dw_helpers.count_committed_with_vectors: exit",
        extra={"_fields": {"vectored": count}},
    )
    return count


async def count_committed_facts(db: DbPool) -> int:
    """Total committed-fact rows — sizes the contradiction-scan strategy.

    Below the ANN threshold the brute-force O(n^2) scan is used; at/above it the
    incremental watermark scan kicks in. The ``committed_facts`` read lives here
    with the rest of the dual-bridge SQL.
    """
    rows = await db.fetch_all("SELECT COUNT(*) AS n FROM committed_facts")
    count = int(rows[0]["n"]) if rows else 0
    log.memory.debug(
        "[memory] dw_helpers.count_committed_facts: exit",
        extra={"_fields": {"total": count}},
    )
    return count
