"""DNA hydration: overlay persisted owl_dna onto the live registry at boot.

apply_dna_overlay is the SINGLE DNA-only overlay primitive — reused by this
hydrator AND by EvolutionCoordinator's live-refresh (Task 4)."""
from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.dna import _MUTABLE_TRAITS, OwlDNA
from stackowl.owls.registry import OwlRegistry

# Canonical owl_dna read — the one place that knows the trait column list.
_SELECT_ALL_DNA = (
    "SELECT owl_name, challenge_level, verbosity, curiosity, formality, "
    "creativity, precision FROM owl_dna"
)


async def read_all_owl_dna(db: DbPool) -> dict[str, dict[str, float]]:
    """Fetch every row from owl_dna; return mapping owl_name → trait dict."""
    rows = await db.fetch_all(_SELECT_ALL_DNA, ())
    return {str(r["owl_name"]): {t: r[t] for t in _MUTABLE_TRAITS} for r in rows}


def apply_dna_overlay(registry: OwlRegistry, owl_name: str, dna: OwlDNA) -> bool:
    """DNA-only overlay: get current manifest → model_copy(dna) → replace.

    Returns False if the owl isn't registered (orphan). This is the single
    overlay primitive — reused by DnaHydrator (boot) and EvolutionCoordinator
    (live-refresh, Task 4). Identity fields (role, system_prompt, …) are never
    touched; only the ``dna`` slot is replaced.
    """
    log.startup.debug(
        "[owls] dna_hydrator.apply_dna_overlay: entry",
        extra={"_fields": {"owl": owl_name}},
    )
    try:
        current = registry.get(owl_name)
    except Exception:
        log.startup.debug(
            "[owls] dna_hydrator.apply_dna_overlay: orphan — not in registry",
            extra={"_fields": {"owl": owl_name}},
        )
        return False
    registry.replace(current.model_copy(update={"dna": dna}))
    log.startup.debug(
        "[owls] dna_hydrator.apply_dna_overlay: exit — dna replaced",
        extra={"_fields": {"owl": owl_name}},
    )
    return True


def _coerce_dna(base: OwlDNA, row: dict[str, float]) -> OwlDNA:
    """Build DNA from a persisted row: clamp each trait to [0, 1].

    Decision: NaN / ±inf / non-numeric / missing → keep base value for that
    trait. (model_copy won't re-validate, so clamp explicitly with v != v NaN
    guard.) decay_rate_per_week is NOT in owl_dna columns; the base keeps it.
    """
    updates: dict[str, float] = {}
    for trait in _MUTABLE_TRAITS:
        v = row.get(trait)
        if (
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or v != v  # NaN guard
            or v in (float("inf"), float("-inf"))
        ):
            continue
        updates[trait] = max(0.0, min(1.0, float(v)))
    return base.model_copy(update=updates)


async def hydrate_dna(registry: OwlRegistry, db: DbPool) -> int:
    """Overlay persisted owl_dna onto registry manifests at boot.

    Fail-safe per row: a corrupt value is clamped; an orphan owl_dna row is
    skipped with a warning; a per-row exception keeps the authored DNA and logs.
    One bad row never aborts the rest or crashes boot.

    Returns the count of owls successfully hydrated.
    """
    log.startup.debug("[owls] hydrate_dna: entry")
    hydrated = 0
    try:
        all_dna = await read_all_owl_dna(db)
    except Exception as exc:
        log.startup.warning(
            "[owls] hydrate_dna: read failed — authored DNA kept",
            exc_info=exc,
        )
        return 0
    log.startup.debug(
        "[owls] hydrate_dna: rows fetched",
        extra={"_fields": {"row_count": len(all_dna)}},
    )
    for name, traits in all_dna.items():
        try:
            current = registry.get(name)
        except Exception:
            log.startup.warning(
                "[owls] hydrate_dna: orphan owl_dna row skipped",
                extra={"_fields": {"owl": name}},
            )
            continue
        try:
            coerced = _coerce_dna(current.dna, traits)
            registry.replace(current.model_copy(update={"dna": coerced}))
            hydrated += 1
            log.startup.debug(
                "[owls] hydrate_dna: owl hydrated",
                extra={"_fields": {"owl": name}},
            )
        except Exception as exc:
            log.startup.warning(
                "[owls] hydrate_dna: row failed — authored DNA kept",
                exc_info=exc,
                extra={"_fields": {"owl": name}},
            )
    log.startup.info(
        "[owls] hydrate_dna: exit",
        extra={"_fields": {"hydrated": hydrated}},
    )
    return hydrated
