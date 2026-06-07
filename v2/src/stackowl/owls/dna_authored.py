"""Durable AUTHORED (baseline) DNA store: the envelope anchor + reset-dna target.

Captured from the YAML/manifest at boot (before hydrate overwrites live DNA) and at owl
creation. Reuses _coerce_dna + upsert_owl_dna (DRY). Fail-safe per owl.
"""
from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.owls.dna_hydrator import _coerce_dna
from stackowl.owls.dna_storage import upsert_owl_dna
from stackowl.owls.registry import OwlRegistry

_SELECT_AUTHORED = (
    "SELECT challenge_level, verbosity, curiosity, formality, creativity, precision "
    "FROM owl_dna_authored WHERE owl_name = ?"
)


async def capture_one_authored(db: DbPool, owl_name: str, dna: OwlDNA) -> None:
    """Idempotent upsert of one owl's authored DNA.

    manifest.dna is already a validated OwlDNA, so this can't write garbage.
    Fail-safe — logs and continues on any exception.
    """
    log.engine.debug(
        "[owls] capture_one_authored: entry",
        extra={"_fields": {"owl": owl_name}},
    )
    try:
        await upsert_owl_dna(db, owl_name, dna, table="owl_dna_authored")
    except Exception as exc:
        log.engine.error(
            "[owls] capture_one_authored failed",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name}},
        )
        return
    log.engine.debug(
        "[owls] capture_one_authored: exit",
        extra={"_fields": {"owl": owl_name}},
    )


async def capture_authored_dna(registry: OwlRegistry, db: DbPool) -> int:
    """Boot pass: capture each registered owl's authored DNA BEFORE hydrate overwrites it.

    Idempotent. Returns count captured. One bad owl never aborts the loop.
    """
    log.engine.debug("[owls] capture_authored_dna: entry")
    captured = 0
    for manifest in list(registry.all()):
        try:
            await capture_one_authored(db, manifest.name, manifest.dna)
            captured += 1
        except Exception as exc:
            log.engine.error(
                "[owls] capture_authored_dna: owl failed",
                exc_info=exc,
                extra={"_fields": {"owl": manifest.name}},
            )
    log.engine.debug(
        "[owls] capture_authored_dna: exit",
        extra={"_fields": {"captured": captured}},
    )
    return captured


async def read_authored_dna(db: DbPool, owl_name: str) -> OwlDNA | None:
    """Read an owl's authored DNA, coerced (NaN/inf/out-of-range guarded).

    Returns None if no row exists for this owl.
    """
    log.engine.debug(
        "[owls] read_authored_dna: entry",
        extra={"_fields": {"owl": owl_name}},
    )
    try:
        rows = await db.fetch_all(_SELECT_AUTHORED, (owl_name,))
    except Exception as exc:
        log.engine.error(
            "[owls] read_authored_dna failed",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name}},
        )
        return None
    if not rows:
        log.engine.debug(
            "[owls] read_authored_dna: no row",
            extra={"_fields": {"owl": owl_name}},
        )
        return None
    row = {t: rows[0][t] for t in TRAIT_NAMES}
    result = _coerce_dna(OwlDNA(), row)
    log.engine.debug(
        "[owls] read_authored_dna: exit",
        extra={"_fields": {"owl": owl_name}},
    )
    return result
