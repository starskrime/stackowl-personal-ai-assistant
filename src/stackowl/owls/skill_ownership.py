"""Skill-ownership hydration: overlay persisted skill_ownership onto the live
registry at boot, and the durable write used when the synthesizer attaches a
learned skill to its owning owl (PA4b).

Mirrors the DNA subsystem exactly (dna_hydrator + dna_storage): a live overlay
primitive (:func:`attach_skill_to_owl`), a durable read/write pair
(:func:`persist_skill_ownership` / :func:`read_all_skill_ownership`) and a boot
hydrator (:func:`hydrate_skill_ownership`). No parallel persistence approach.

owner_id scoping is MANDATORY on every read/write: without it a boot hydration
would read every principal's attachments into the requesting principal's
registry (tenant leak) — same rationale as dna_hydrator's owner predicate.
"""
from __future__ import annotations

import time

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.owls.registry import OwlRegistry
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

# owner_id predicate is mandatory (tenant isolation). One canonical read.
_SELECT_ALL = "SELECT owl_name, skill_name FROM skill_ownership WHERE owner_id = ?"
# INSERT OR IGNORE makes the write idempotent against the (owner_id, owl_name,
# skill_name) primary key — a repeat attach is a no-op, never an error.
_UPSERT = (
    "INSERT OR IGNORE INTO skill_ownership "
    "(owner_id, owl_name, skill_name, attached_at) VALUES (?, ?, ?, ?)"
)
# Which owls own a given skill (so a deletion can detach it live too).
_SELECT_OWLS_FOR_SKILL = (
    "SELECT owl_name FROM skill_ownership WHERE owner_id = ? AND skill_name = ?"
)
_DELETE_SKILL = "DELETE FROM skill_ownership WHERE owner_id = ? AND skill_name = ?"


def attach_skill_to_owl(
    registry: OwlRegistry, owl_name: str, skill_name: str
) -> bool:
    """Live overlay: add *skill_name* to the owl's ``skills`` tuple (ownership).

    The single overlay primitive — reused by the synthesizer (live attach) and
    :func:`hydrate_skill_ownership` (boot). Returns True when the manifest was
    changed; False when the owl is an orphan (not registered) or already owns the
    skill (idempotent). Never raises — an orphan is logged and skipped.
    """
    log.startup.debug(
        "[owls] skill_ownership.attach: entry",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )
    try:
        current = registry.get(owl_name)
    except Exception:
        log.startup.debug(
            "[owls] skill_ownership.attach: orphan — owl not in registry",
            extra={"_fields": {"owl": owl_name, "skill": skill_name}},
        )
        return False
    if skill_name in current.skills:
        log.startup.debug(
            "[owls] skill_ownership.attach: already owned — no-op",
            extra={"_fields": {"owl": owl_name, "skill": skill_name}},
        )
        return False
    registry.replace(
        current.model_copy(update={"skills": (*current.skills, skill_name)})
    )
    log.startup.debug(
        "[owls] skill_ownership.attach: exit — skill attached",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )
    return True


def detach_skill_from_owl(
    registry: OwlRegistry, owl_name: str, skill_name: str
) -> bool:
    """Live inverse of :func:`attach_skill_to_owl`: drop *skill_name* from the
    owl's ``skills`` tuple. Returns True when the manifest changed; False when the
    owl is an orphan or never owned the skill. Never raises."""
    log.startup.debug(
        "[owls] skill_ownership.detach: entry",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )
    try:
        current = registry.get(owl_name)
    except Exception:
        log.startup.debug(
            "[owls] skill_ownership.detach: orphan — owl not in registry",
            extra={"_fields": {"owl": owl_name, "skill": skill_name}},
        )
        return False
    if skill_name not in current.skills:
        log.startup.debug(
            "[owls] skill_ownership.detach: not owned — no-op",
            extra={"_fields": {"owl": owl_name, "skill": skill_name}},
        )
        return False
    registry.replace(
        current.model_copy(
            update={"skills": tuple(s for s in current.skills if s != skill_name)}
        )
    )
    log.startup.debug(
        "[owls] skill_ownership.detach: skill detached",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )
    return True


async def purge_skill_ownership(
    db: DbPool,
    skill_name: str,
    *,
    registry: OwlRegistry | None = None,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> int:
    """Remove *skill_name* ownership everywhere it was recorded (the deletion path).

    Without this a deprecated/deleted skill's rows linger and the boot hydrator
    re-attaches a now-dead skill name to its owl forever (phantom ownership).
    Detaches the name live from every owl that owned it (when *registry* is
    given) THEN deletes the durable rows. Idempotent — a skill no owl owns is a
    clean no-op. Returns the number of owls detached live.
    """
    log.engine.debug(
        "[skills] purge_skill_ownership: entry",
        extra={"_fields": {"skill": skill_name}},
    )
    detached = 0
    try:
        rows = await db.fetch_all(_SELECT_OWLS_FOR_SKILL, (owner_id, skill_name))
        if registry is not None:
            for r in rows:
                if detach_skill_from_owl(registry, str(r["owl_name"]), skill_name):
                    detached += 1
        await db.execute(_DELETE_SKILL, (owner_id, skill_name))
    except Exception as exc:
        log.engine.error(
            "[skills] purge_skill_ownership: failed",
            exc_info=exc,
            extra={"_fields": {"skill": skill_name}},
        )
        raise
    log.engine.info(
        "[skills] purge_skill_ownership: exit",
        extra={"_fields": {"skill": skill_name, "detached": detached}},
    )
    return detached


async def persist_skill_ownership(
    db: DbPool,
    owl_name: str,
    skill_name: str,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> None:
    """Durably record that *owl_name* owns *skill_name* (idempotent upsert).

    Scoped to *owner_id* (defaults to the single-user
    :data:`~stackowl.tenancy.principal.DEFAULT_PRINCIPAL_ID`). The timestamp
    uses ``time.time()`` to match the synthesizer's clock (REAL column).
    """
    log.engine.debug(
        "[skills] persist_skill_ownership: entry",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )
    try:
        await db.execute(_UPSERT, (owner_id, owl_name, skill_name, time.time()))
    except Exception as exc:
        log.engine.error(
            "[skills] persist_skill_ownership: db write failed",
            exc_info=exc,
            extra={"_fields": {"owl": owl_name, "skill": skill_name}},
        )
        raise
    log.engine.debug(
        "[skills] persist_skill_ownership: exit",
        extra={"_fields": {"owl": owl_name, "skill": skill_name}},
    )


async def read_all_skill_ownership(
    db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID
) -> dict[str, list[str]]:
    """Fetch skill_ownership rows for *owner_id*; map owl_name -> skill names."""
    rows = await db.fetch_all(_SELECT_ALL, (owner_id,))
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["owl_name"]), []).append(str(r["skill_name"]))
    return out


async def owl_drive_thresholds(
    db: DbPool,
    registry: OwlRegistry,
    base: float,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> dict[str, float]:
    """Per-skill retirement threshold, nudged by the owning owl(s)' current
    ``completion_drive`` trait (AD-7, Story 3.5 — the DNA -> skill mirror of
    3.4's skill -> DNA direction).

    ``effective = base * (1.0 - 0.2 * (avg_completion_drive - 0.5))`` — a
    highly-persistent owl (drive -> 1.0) gets a more LENIENT (lower) threshold, so
    its skill must be worse before it is retired; a low-persistence owl gets a
    stricter one. At the neutral default (0.5) this returns ``base`` unchanged,
    which is why the map only carries skills that actually differ.

    MOVED HERE from ``SkillSynthesizer._effective_deprecate_threshold`` in D09.3
    slice 3, when retirement became the curator's single job (X11). It lives in
    the ownership module rather than in ``skills.lifecycle`` deliberately: the
    curator must stay owl-agnostic and take a plain mapping, or the decay pass
    acquires a dependency on the owl registry it does not otherwise need.

    Never raises. No ownership rows, an unwired registry, or an orphaned row
    (owl name no longer live) all degrade to "no opinion" for that skill —
    one bad row must never cost the whole pass its thresholds.
    """
    # 1. ENTRY
    log.owls.debug("[ownership] owl_drive_thresholds: entry",
                   extra={"_fields": {"base": base}})
    try:
        owl_to_skills = await read_all_skill_ownership(db, owner_id)
    except Exception as exc:  # B5 — degrade to the flat threshold, do not abort
        log.owls.warning(
            "[ownership] owl_drive_thresholds: ownership read failed — every "
            "skill falls back to the flat threshold",
            exc_info=exc,
        )
        return {}

    skill_to_drives: dict[str, list[float]] = {}
    for owl_name, skill_names in owl_to_skills.items():
        try:
            drive = registry.get(owl_name).dna.completion_drive
        except Exception as exc:  # OwlNotFoundError and anything else
            log.owls.debug(
                "[ownership] owl_drive_thresholds: orphaned ownership row — skipped",
                exc_info=exc, extra={"_fields": {"owl": owl_name}},
            )
            continue
        for skill_name in skill_names:
            skill_to_drives.setdefault(skill_name, []).append(drive)

    out = {
        name: base * (1.0 - 0.2 * (sum(drives) / len(drives) - 0.5))
        for name, drives in skill_to_drives.items()
        if drives
    }
    # 4. EXIT
    log.owls.debug("[ownership] owl_drive_thresholds: exit",
                   extra={"_fields": {"skills": len(out)}})
    return out


async def hydrate_skill_ownership(
    registry: OwlRegistry,
    db: DbPool,
    owner_id: str = DEFAULT_PRINCIPAL_ID,
) -> int:
    """Overlay persisted skill_ownership onto registry manifests at boot.

    Scoped to *owner_id* so only that principal's attachments are loaded (no
    cross-tenant bleed). Fail-safe per row: an orphan owl row is skipped with a
    warning, a per-row exception is logged and skipped — one bad row never
    aborts the rest or crashes boot. Returns the count of skills attached.
    """
    log.startup.debug(
        "[owls] hydrate_skill_ownership: entry",
        extra={"_fields": {"owner_id": owner_id}},
    )
    attached = 0
    try:
        all_owned = await read_all_skill_ownership(db, owner_id)
    except Exception as exc:
        log.startup.warning(
            "[owls] hydrate_skill_ownership: read failed — no attachments applied",
            exc_info=exc,
        )
        return 0
    log.startup.debug(
        "[owls] hydrate_skill_ownership: rows fetched",
        extra={"_fields": {"owner_id": owner_id, "owl_count": len(all_owned)}},
    )
    for owl_name, skills in all_owned.items():
        for skill_name in skills:
            try:
                if attach_skill_to_owl(registry, owl_name, skill_name):
                    attached += 1
            except Exception as exc:
                log.startup.warning(
                    "[owls] hydrate_skill_ownership: row failed — skipped",
                    exc_info=exc,
                    extra={"_fields": {"owl": owl_name, "skill": skill_name}},
                )
    log.startup.info(
        "[owls] hydrate_skill_ownership: exit",
        extra={"_fields": {"owner_id": owner_id, "attached": attached}},
    )
    return attached
