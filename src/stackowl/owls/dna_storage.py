"""upsert_owl_dna — the single shared helper that writes the 6 trait columns
into either ``owl_dna`` (evolved store) or ``owl_dna_authored`` (baseline/
authored store). Centralising the upsert here removes the duplicate
``_UPSERT_DNA_SQL`` / ``_persist_dna`` logic from ``evolution.py`` (DRY).
"""

from __future__ import annotations

from datetime import UTC, datetime

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

# ---------------------------------------------------------------------------
# Shared upsert helper — owl_dna and owl_dna_authored
# ---------------------------------------------------------------------------

_ALLOWED_DNA_TABLES: frozenset[str] = frozenset({"owl_dna", "owl_dna_authored"})
# Residual DRY-cleanup-C site (tests/owls/test_dna_defaults.py pins every
# duplicate trait-order copy in the codebase against the canonical
# TRAIT_NAMES) — kept even though DNACheckpointer (its only prior consumer)
# is gone; the guard test still imports this name.
_DNA_FIELDS: tuple[str, ...] = TRAIT_NAMES


async def upsert_owl_dna(
    db: DbPool,
    owl_name: str,
    dna: OwlDNA,
    *,
    table: str = "owl_dna",
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> None:
    """Upsert the 6 trait columns + updated_at for an owl into *table*.

    *table* must be one of ``"owl_dna"`` (evolved store) or
    ``"owl_dna_authored"`` (authored/baseline store). The column order follows
    the canonical :data:`~stackowl.owls.dna_defaults.TRAIT_NAMES` tuple so
    positional transposition is impossible.

    ``owner_id`` is written EXPLICITLY on insert (defaults to
    :data:`~stackowl.tenancy.principal.DEFAULT_PRINCIPAL_ID`, matching
    ``dna_hydrator.py``'s read-side default) rather than left to the SQL
    column's ``DEFAULT`` — the read side scopes every query with
    ``WHERE owner_id = ?``, so relying on the two independently-maintained
    defaults happening to coincide is a latent multi-tenant bug waiting to
    happen the moment either one changes.

    Raises :class:`ValueError` for any unknown table name (SQL-injection guard).
    """
    log.engine.debug(
        "[dna] upsert_owl_dna: entry",
        extra={"_fields": {"owl": owl_name, "table": table, "owner_id": owner_id}},
    )
    if table not in _ALLOWED_DNA_TABLES:
        raise ValueError(f"upsert_owl_dna: unknown table {table!r}")

    cols = ", ".join(TRAIT_NAMES)
    placeholders = ", ".join("?" for _ in TRAIT_NAMES)
    set_clause = ", ".join(f"{t} = excluded.{t}" for t in TRAIT_NAMES)
    sql = (
        f"INSERT INTO {table} (owl_name, {cols}, updated_at, owner_id) "
        f"VALUES (?, {placeholders}, ?, ?) "
        f"ON CONFLICT(owl_name) DO UPDATE SET {set_clause}, updated_at = excluded.updated_at"
    )
    values = (
        owl_name,
        *(float(getattr(dna, t)) for t in TRAIT_NAMES),
        datetime.now(UTC).isoformat(),
        owner_id,
    )
    try:
        await db.execute(sql, values)
    except Exception as exc:
        log.engine.error(
            "[dna] upsert_owl_dna: db write failed",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name, "table": table}},
        )
        raise
    log.engine.debug(
        "[dna] upsert_owl_dna: exit",
        extra={"_fields": {"owl": owl_name, "table": table}},
    )
