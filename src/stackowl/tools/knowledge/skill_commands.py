"""``skill.*`` ``CommandSpec``s -- the 12 command types shared by the
``skill_manage`` tool and the ``/skill`` slash command (Story 4.9, AD-1/
AD-26).

ONE SHARED NAMESPACE (``authz/state_change_census.py``'s own docstring): a
command-type string names the same real action regardless of which surface
submits it — ``skill.delete`` is both ``skill_manage(action="delete")`` and
``/skill rm``, ``skill.set_enabled`` is both ``skill_manage(action="enable"/
"disable")`` and ``/skill enable``/``/skill disable``. Every handler below is
therefore reused by BOTH callers; only the payload each surface resolves
before submitting differs.

Reuses ``skill_helpers.py``'s existing mutate-with-provenance chokepoint
(``record_skill_mutation``) and install/restore helpers, and ``SkillIndexStore``'s
``.delete``/``.set_enabled``/``.set_pinned`` as the actual mutators — never a
new parallel mechanism.

PLACEMENT: here (``tools/knowledge/``), not ``commands/spec/`` — AD-7's
import-boundary rationale is identical to every other migrated subsystem's
own ``commands.py``: a SUBSYSTEM declaring its own commands may freely import
both ``commands.spec`` (to register into) and the skills subsystem (the
mutators it wraps).

RECEIPT SHAPE: read-check-first / write-last (mirrors ``owl_build_commands.
py``/``notifications/commands.py``'s own Design Notes) — a skill mutation
touches the filesystem AND (via ``record_skill_mutation``) a separate
``skill_audit`` DB write in two calls that are not transactional together.

Importing this module registers all 12 ``CommandSpec``s and handlers as a
side effect — imported once from ``startup/orchestrator.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.skill_helpers import (
    SkillInstallError,
    install_from_archive_url,
    install_from_git_url,
    install_from_local_path,
    record_skill_mutation,
    reindex_after_change,
    restore_snapshot,
)
from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log

AUTHOR_CREATE = "skill.author_create"
AUTHOR_EDIT = "skill.author_edit"
AUTHOR_PATCH = "skill.author_patch"
DELETE = "skill.delete"
SET_ENABLED = "skill.set_enabled"
SYNTHESIZE = "skill.synthesize"
INSTALL = "skill.install"
RELOAD_INDEX = "skill.reload_index"
SET_PINNED = "skill.set_pinned"
DEDUPE = "skill.dedupe"
MIGRATE_STANDARD = "skill.migrate_standard"
RESTORE_VERSION = "skill.restore_version"

_ALREADY_ATTEMPTED_ERROR = (
    "command already attempted once — the original outcome was not "
    "recorded and is not reconfirmed by this call"
)
_SKILL_MD = "SKILL.md"


def _normalized(content: str) -> str:
    return content.rstrip("\n") + "\n"


async def _already_executed(db: Any, command_id: str) -> bool:
    """Read-only ``command_receipts`` check -- no side effect."""
    log.skills.debug(
        "[commands] skill_commands._already_executed: entry",
        extra={"_fields": {"command_id": command_id}},
    )
    rows = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (command_id,),
    )
    found = bool(rows)
    log.skills.debug(
        "[commands] skill_commands._already_executed: exit",
        extra={"_fields": {"command_id": command_id, "already_executed": found}},
    )
    return found


async def _reindex(store: Any, skills_root: Path) -> str:
    """Rescan disk so the change is searchable — mirrors ``skill_manage.py``'s
    OWN pre-migration ``self._reindex``: retried once, then degrades to a
    structured "reindex pending" note (empty string on success) rather than
    ever failing the mutation that already landed. A ``ToolRegistrationError``
    (a tool-name collision) is NOT a transient reindex failure — surfaced as
    its own distinct, actionable note and never retried (PLUG-3/F047).

    ``skills_root`` is ALWAYS the caller's own (the tool's ``StackowlHome.
    skills_dir()``, or ``/skill``'s constructor-injected ``self._root``) —
    never hard-coded here, so a caller wired to a non-default root (every
    test in this tree, and any future non-default deployment) reindexes the
    SAME tree its write just landed in."""
    from stackowl.exceptions import ToolRegistrationError
    from stackowl.pipeline.services import get_services
    from stackowl.skills.loader import SkillLoader

    services = get_services()
    loader = SkillLoader(tool_registry=services.tool_registry, owl_registry=services.owl_registry)
    for attempt in (1, 2):
        try:
            await reindex_after_change(
                loader, store, skills_root, embedding_registry=services.embedding_registry,
            )
            return ""
        except ToolRegistrationError as exc:
            log.skills.warning(
                "[commands] skill_commands._reindex: blocked by tool-name collision",
                extra={"_fields": {"tool": exc.tool_name, "reason": exc.reason}},
            )
            return (
                f" NOTE: the skill was saved but its tool {exc.tool_name!r} could "
                f"not be registered — {exc.reason}. The skill is NOT yet active; "
                "rename the conflicting tool and re-author."
            )
        except Exception as exc:  # B5 — retry once, then degrade
            log.skills.warning(
                "[commands] skill_commands._reindex: failed",
                exc_info=exc, extra={"_fields": {"attempt": attempt}},
            )
    return (
        " NOTE: the skill was saved and audited but is not yet searchable "
        "(reindex pending — retrieval will pick it up on next boot)."
    )


# =============================================================================
# skill.author_create / skill.author_edit / skill.author_patch
# =============================================================================


class SkillContentPayload(BaseModel):
    """Shared payload for the three content-writing skill mutations — the
    resolved target dir, source and skill_id are ALL supplied by the caller
    (``skill_manage``/``/skill``), which already looked the skill up and ran
    validation/security-scan; the handler's only job is the write."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    content: str
    source: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    #: ``record_skill_mutation``'s own ``op`` — "create" or "update".
    op: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    skill_id: int | None = None
    category: str | None = None
    is_patch: bool = False
    #: The caller's OWN skills root (``StackowlHome.skills_dir()`` for
    #: ``skill_manage``, ``/skill``'s constructor-injected ``self._root``) —
    #: never a global default hard-coded in the handler, so the post-write
    #: reindex scans the SAME tree the write just landed in.
    skills_root: str = Field(min_length=1)


async def _write_content_handler(
    payload: SkillContentPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"skill": payload.name, "already_done": True},
        )

    target_dir = Path(payload.target_dir)

    async def _mutate() -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / _SKILL_MD).write_text(_normalized(payload.content), encoding="utf-8")

    before_hash, _after_hash = await record_skill_mutation(
        store,
        skill_name=payload.name,
        source=payload.source,  # type: ignore[arg-type]
        op=payload.op,
        actor=payload.actor,
        target_dir=target_dir,
        mutate=_mutate,
        snapshot_when="after",
        skill_id=payload.skill_id,
        details=(
            {"category": payload.category} if payload.category
            else ({"patch": True} if payload.is_patch else None)
        ),
    )
    reindex_note = await _reindex(store, Path(payload.skills_root))

    import json

    if payload.op == "create":
        # author_create's undo is skill.delete (mirrors owl_build_commands.py's
        # own create-undoes-to-retire shape) — the skill_id is only known
        # AFTER the reindex above upserts it into the index, so it is looked
        # up here rather than carried on the forward payload.
        created = await store.get(payload.source, payload.name)  # type: ignore[arg-type]
        if created is None:
            # Review finding (2026-09-22 pass): _reindex already retried
            # internally; one more lookup covers a genuine (if rare) read
            # right after that internal retry's own write. A second miss
            # means the skill really isn't indexed yet (matches
            # reindex_note's own degraded-note case) — undo_payload stays
            # None honestly rather than guessing a skill_id, but this is now
            # LOGGED (not silent), since SkillDeletePayload has no default
            # for skill_id and the fallback (resubmitting this create's own
            # payload) would otherwise fail request_undo with an unhandled
            # validation error.
            created = await store.get(payload.source, payload.name)  # type: ignore[arg-type]
            if created is None:
                log.skills.warning(
                    "[commands] skill_commands._write_content_handler: "
                    "created skill not found post-reindex — undo will be "
                    "unavailable for this command",
                    extra={"_fields": {"skill": payload.name, "source": payload.source}},
                )
        undo_payload = (
            json.dumps({
                "name": payload.name, "source": payload.source,
                "target_dir": payload.target_dir, "skill_id": created.skill_id,
                "actor": payload.actor, "skills_root": payload.skills_root,
            })
            if created is not None else None
        )
    else:
        # Boundaries — author_edit/author_patch's undo reuses the EXISTING
        # skill.restore_version type (never a new parallel snapshot
        # mechanism): the CAPTURED pre-mutation hash names the audit entry
        # whose OWN snapshot is this edit's "restore-to" state
        # (find_audit_by_hash matches an earlier entry's after_hash against it).
        undo_payload = (
            json.dumps({
                "name": payload.name, "version": before_hash,
                "skills_root": payload.skills_root,
            })
            if before_hash else None
        )
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(
        success=True, result={"skill": payload.name, "reindex_note": reindex_note},
    )


# =============================================================================
# skill.delete
# =============================================================================


class SkillDeletePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    skill_id: int
    actor: str = Field(min_length=1)
    skills_root: str = Field(min_length=1)


async def _delete_handler(
    payload: SkillDeletePayload, context: CommandContext,
) -> CommandOutcome:
    import shutil

    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"skill": payload.name, "already_done": True},
        )

    target_dir = Path(payload.target_dir)

    async def _mutate() -> None:
        shutil.rmtree(target_dir, ignore_errors=True)
        await store.delete(payload.skill_id)

    before_hash, _after_hash = await record_skill_mutation(
        store,
        skill_name=payload.name,
        source=payload.source,  # type: ignore[arg-type]
        op="delete",
        actor=payload.actor,
        target_dir=target_dir,
        mutate=_mutate,
        snapshot_when="before",
        skill_id=payload.skill_id,
        details={"path": str(target_dir)},
    )
    # Review call (Design Notes): a shared command type unifies its two
    # callers' behavior — reindexing after every delete (skill_manage.py's
    # own pre-existing behavior) keeps the index in sync for BOTH callers
    # rather than leaving /skill rm's deletions stale until the next reload.
    reindex_note = await _reindex(store, Path(payload.skills_root))

    # Boundaries — delete's undo reuses skill.restore_version too, keyed the
    # SAME way as author_edit/author_patch above (the captured pre-mutation
    # hash names an audit entry carrying this exact content's snapshot —
    # here, this delete's OWN row, since snapshot_when="before" captured it).
    import json

    undo_payload = (
        json.dumps({
            "name": payload.name, "version": before_hash, "skills_root": payload.skills_root,
        })
        if before_hash else None
    )
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(
        success=True, result={"skill": payload.name, "reindex_note": reindex_note},
    )


# =============================================================================
# skill.set_enabled
# =============================================================================


class SkillSetEnabledPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target_dir: str = Field(min_length=1)
    skill_id: int
    enabled: bool
    actor: str = Field(min_length=1)


async def _set_enabled_handler(
    payload: SkillSetEnabledPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"skill": payload.name, "already_done": True},
        )

    verb = "enable" if payload.enabled else "disable"

    async def _mutate() -> None:
        await store.set_enabled(payload.skill_id, enabled=payload.enabled)

    await record_skill_mutation(
        store,
        skill_name=payload.name,
        source=payload.source,  # type: ignore[arg-type]
        op=verb,
        actor=payload.actor,
        target_dir=Path(payload.target_dir),
        mutate=_mutate,
        snapshot_when="none",
        skill_id=payload.skill_id,
    )

    # Reversible → itself: undo re-submits skill.set_enabled with `enabled`
    # flipped back — a trivial, always-safe toggle (mirrors scheduling.
    # pause_job/resume_job's own opposite-type shape, just within one type).
    import json

    undo_payload = json.dumps({
        "name": payload.name, "source": payload.source, "target_dir": payload.target_dir,
        "skill_id": payload.skill_id, "enabled": not payload.enabled, "actor": payload.actor,
    })
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"skill": payload.name, "enabled": payload.enabled})


# =============================================================================
# skill.set_pinned
# =============================================================================


class SkillSetPinnedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    skill_id: int
    pinned: bool
    actor: str = Field(min_length=1)


async def _set_pinned_handler(
    payload: SkillSetPinnedPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"skill": payload.name, "already_done": True},
        )
    verb = "pin" if payload.pinned else "unpin"
    await store.set_pinned(payload.skill_id, payload.pinned)
    await store.audit_write(
        skill_name=payload.name, source=payload.source, op=verb, actor=payload.actor,  # type: ignore[arg-type]
    )

    # Reversible → itself: undo re-submits skill.set_pinned with `pinned`
    # flipped back (mirrors skill.set_enabled's own shape above).
    import json

    undo_payload = json.dumps({
        "name": payload.name, "source": payload.source, "skill_id": payload.skill_id,
        "pinned": not payload.pinned, "actor": payload.actor,
    })
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"skill": payload.name, "pinned": payload.pinned})


# =============================================================================
# skill.reload_index
# =============================================================================


class SkillReloadPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    skills_root: str = Field(min_length=1)


async def _reload_handler(
    payload: SkillReloadPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services
    from stackowl.skills.loader import SkillLoader

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(success=False, error=_ALREADY_ATTEMPTED_ERROR, result={"already_done": True})

    loader = SkillLoader(tool_registry=services.tool_registry, owl_registry=services.owl_registry)
    loaded = await reindex_after_change(
        loader, store, Path(payload.skills_root), embedding_registry=services.embedding_registry,
    )

    # Reversible → itself: a rescan is idempotent (re-running it undoes
    # nothing but is always safe), so it never needs to block on step-up.
    import json

    undo_payload = json.dumps({"skills_root": payload.skills_root})
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"loaded": len(loaded)})


# =============================================================================
# skill.install
# =============================================================================


class SkillInstallPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: "local" | "git" | "archive" — resolved by ``/skill add``'s own
    #: dispatch (URL-scheme sniffing) before submission.
    kind: str = Field(min_length=1)
    source: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    skills_root: str = Field(min_length=1)


async def _install_handler(
    payload: SkillInstallPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR, result={"already_done": True},
        )

    root = Path(payload.skills_root)
    try:
        if payload.kind == "git":
            result = await install_from_git_url(payload.source, root)
        elif payload.kind == "archive":
            result = await install_from_archive_url(payload.source, root)
        else:
            result = await install_from_local_path(Path(payload.source), root)
    except SkillInstallError as exc:
        return CommandOutcome(success=False, error=str(exc))

    reindex_note_holder: list[str] = []

    async def _reindex_mutate() -> None:
        reindex_note_holder.append(await _reindex(store, root))

    await record_skill_mutation(
        store,
        skill_name=result.name, source="installed", op="create",
        actor=payload.actor, target_dir=result.path,
        mutate=_reindex_mutate, snapshot_when="after",
        details={"path": str(result.path)},
    )

    # Reversible: a freshly installed skill's honest undo is deleting it
    # (mirrors skill.author_create's own undo=skill.delete shape) — the
    # skill_id is only known AFTER the reindex above upserts it.
    import json

    installed = await store.get("installed", result.name)
    if installed is None:
        # Same graceful-degradation shape as _write_content_handler's own
        # create branch (review finding, 2026-09-22 pass): one extra lookup,
        # then an observable warning rather than a silent None.
        installed = await store.get("installed", result.name)
        if installed is None:
            log.skills.warning(
                "[commands] skill_commands._install_handler: installed skill "
                "not found post-reindex — undo will be unavailable for this "
                "command",
                extra={"_fields": {"skill": result.name}},
            )
    undo_payload = (
        json.dumps({
            "name": result.name, "source": "installed", "target_dir": str(result.path),
            "skill_id": installed.skill_id, "actor": payload.actor,
            "skills_root": payload.skills_root,
        })
        if installed is not None else None
    )
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(
        success=True,
        result={
            "name": result.name, "path": str(result.path), "kind": payload.kind,
            "reindex_note": reindex_note_holder[0] if reindex_note_holder else "",
        },
    )


# =============================================================================
# skill.restore_version
# =============================================================================


class SkillRestoreVersionPayload(BaseModel):
    """``{name, version}`` is deliberately the WHOLE shape — this is exactly
    what ``request_undo`` resubmits from an ``author_edit``/``author_patch``/
    ``delete`` command's captured ``undo_payload`` (Boundaries), and exactly
    what ``/skill restore <name> --version <hash>`` already had on hand
    without looking anything else up first. The handler does the SAME
    ``find_audit_by_hash`` lookup ``/skill restore`` used to do inline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    actor: str = "user:restore"
    skills_root: str = Field(min_length=1)


async def _restore_version_handler(
    payload: SkillRestoreVersionPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.commands.skill_helpers import hash_dir
    from stackowl.pipeline.services import get_services

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"skill": payload.name, "already_done": True},
        )

    entry = await store.find_audit_by_hash(payload.name, payload.version)
    if entry is None:
        return CommandOutcome(
            success=False,
            error=f"no audit entry matches hash {payload.version!r} for '{payload.name}'.",
        )
    if not entry.snapshot:
        return CommandOutcome(
            success=False,
            error=f"audit entry {entry.audit_id} ({entry.op} by {entry.actor}) has no "
                  "snapshot — this op didn't change file content.",
        )
    if entry.source == "builtin":
        return CommandOutcome(success=False, error="built-in skills are read-only.")

    target_dir = Path(payload.skills_root) / entry.source / payload.name
    before = hash_dir(target_dir) if target_dir.exists() else None
    try:
        restore_snapshot(target_dir, entry.snapshot)
    except Exception as exc:  # B5
        log.skills.error(
            "[commands] skill_commands._restore_version_handler: restore_snapshot "
            "failed",
            exc_info=exc, extra={"_fields": {"skill": payload.name}},
        )
        return CommandOutcome(success=False, error=f"write failed: {exc}")

    reindex_note_holder: list[str] = []

    async def _reindex_mutate() -> None:
        reindex_note_holder.append(await _reindex(store, Path(payload.skills_root)))

    await record_skill_mutation(
        store,
        skill_name=payload.name, source=entry.source, op="restore",
        actor=payload.actor, target_dir=target_dir,
        mutate=_reindex_mutate, snapshot_when="after",
        snapshot=entry.snapshot, before_hash=before,
        details={
            "restored_from_audit_id": entry.audit_id,
            "restored_from_op": entry.op,
            "restored_from_actor": entry.actor,
            "restored_hash": payload.version,
        },
    )

    # Reversible → itself: undo re-submits skill.restore_version with the
    # CAPTURED pre-restore hash (mirrors skill.set_enabled's own toggle-back
    # shape) — restoring back to what was live before THIS restore ran.
    import json

    undo_payload = (
        json.dumps({"name": payload.name, "version": before, "skills_root": payload.skills_root})
        if before else None
    )
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(
        success=True,
        result={
            "skill": payload.name, "restored_from_audit_id": entry.audit_id,
            "files": len(entry.snapshot),
            "reindex_note": reindex_note_holder[0] if reindex_note_holder else "",
        },
    )


# =============================================================================
# skill.dedupe
# =============================================================================


class SkillDedupePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    apply: bool = False
    skills_root: str = Field(min_length=1)


async def _dedupe_handler(
    payload: SkillDedupePayload, context: CommandContext,
) -> CommandOutcome:
    from datetime import UTC, datetime

    from stackowl.pipeline.services import get_services
    from stackowl.skills.consolidation import SkillConsolidator

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(success=False, error=_ALREADY_ATTEMPTED_ERROR, result={"already_done": True})

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    plan = await SkillConsolidator(store, Path(payload.skills_root)).run(
        apply=payload.apply, stamp=stamp,
    )

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    # Pre-formats each family's TWO display lines (describe() + its "dropping:"
    # detail, capped at 6 names) — mirrors /skill dedupe's own pre-migration
    # loop exactly, so the slash command need only join what comes back.
    family_lines: list[str] = []
    for family in plan.families[:40]:
        family_lines.append(f"  {family.describe()}")
        family_lines.append(
            f"      dropping: {', '.join(family.removed[:6])}"
            + (f" (+{len(family.removed) - 6} more)" if len(family.removed) > 6 else "")
        )
    return CommandOutcome(
        success=True,
        result={
            "applied": plan.applied,
            "n_families": len(plan.families),
            "rows_removed": plan.rows_removed,
            "archive_path": str(plan.archive_path) if plan.archive_path else None,
            "summary": plan.summary(),
            "family_lines": family_lines,
            "more_families": max(0, len(plan.families) - 40),
            "skipped": list(plan.skipped),
        },
    )


# =============================================================================
# skill.migrate_standard
# =============================================================================


class SkillMigratePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    apply: bool = False
    limit: int = Field(gt=0)
    skills_root: str = Field(min_length=1)


async def _migrate_handler(
    payload: SkillMigratePayload, context: CommandContext,
) -> CommandOutcome:
    from datetime import UTC, datetime

    from stackowl.pipeline.services import get_services
    from stackowl.skills import standard_migration as migration

    services = get_services()
    store = services.skill_store
    db = services.db_pool
    if store is None or db is None:
        return CommandOutcome(success=False, error="skills unavailable (no store/database configured)")
    provider_registry = services.provider_registry
    if provider_registry is None:
        return CommandOutcome(
            success=False,
            error="no provider registry wired — migration rewrites content and needs a model.",
        )
    if await _already_executed(db, context.command_id):
        return CommandOutcome(success=False, error=_ALREADY_ATTEMPTED_ERROR, result={"already_done": True})

    provider, model = provider_registry.get_with_cascade("fast")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = await migration.SkillStandardMigrator(
        store, provider,
        archive_root=Path(payload.skills_root).parent / "pre-migration",
        model=model, consent_gate=services.consent_gate,
    ).run(apply=payload.apply, limit=payload.limit, stamp=stamp)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(
        success=True,
        result={
            "applied": report.applied,
            "migrated": report.migrated,
            "failed": report.failed,
            "remaining": report.remaining,
            "summary": report.summary(),
            "outcome_lines": [o.describe() for o in report.outcomes],
            "archive_path": str(report.archive_path) if report.archive_path else None,
            "has_outcomes": bool(report.outcomes),
        },
    )


# =============================================================================
# skill.synthesize
# =============================================================================


class SkillSynthesizePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


async def _synthesize_handler(
    payload: SkillSynthesizePayload, context: CommandContext,
) -> CommandOutcome:
    import uuid

    from stackowl.paths import StackowlHome
    from stackowl.pipeline.services import get_services
    from stackowl.scheduler.job import Job
    from stackowl.skills.synthesizer_handler import SkillSynthesizerHandler

    services = get_services()
    missing = [
        label for label, dep in (
            ("db_pool", services.db_pool),
            ("provider_registry", services.provider_registry),
            ("skill_store", services.skill_store),
            ("embedding_registry", services.embedding_registry),
        )
        if dep is None
    ]
    if missing:
        return CommandOutcome(success=False, error=f"learning subsystem not wired: missing {', '.join(missing)}")
    db = services.db_pool
    if db is None:  # already covered by the `missing` check above; narrows for mypy
        return CommandOutcome(success=False, error="learning subsystem not wired: missing db_pool")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(success=False, error=_ALREADY_ATTEMPTED_ERROR, result={"already_done": True})

    handler = SkillSynthesizerHandler(
        db=services.db_pool,  # type: ignore[arg-type]
        provider_registry=services.provider_registry,  # type: ignore[arg-type]
        skill_store=services.skill_store,  # type: ignore[arg-type]
        skills_root=StackowlHome.skills_dir(),
        embedding_registry=services.embedding_registry,
        owl_registry=services.owl_registry,
        consent_gate=services.consent_gate,
    )
    job_id = f"synthesize_skills-{uuid.uuid4().hex}"
    job = Job(
        job_id=job_id, handler_name="skill_synthesizer", schedule="manual",
        idempotency_key=job_id, last_run_at=None, next_run_at="", status="running",
    )
    try:
        result = await handler.execute(job)
    except Exception as exc:  # B5 — degrade, never raise
        log.skills.error(
            "[commands] skill_commands._synthesize_handler: handler failed",
            exc_info=exc,
        )
        return CommandOutcome(success=False, error=f"skill synthesis failed: {type(exc).__name__}: {exc}")

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    if not result.success:
        return CommandOutcome(
            success=False,
            error=f"skill synthesis did not complete: {result.error or 'unknown error'}",
        )
    return CommandOutcome(
        success=True,
        result={"output": result.output or "created:0 refined:0 deprecated:0", **result.metadata},
    )


def _register() -> None:
    # Design Notes (mirrors owl_build_commands.py's own reasoning): every
    # type here is `write`, not `consequential` — skill_manage's/
    # synthesize_skills' OWN ToolManifest.action_severity="consequential"
    # already gates the TOOL call itself (ConsequentialActionGate, before
    # execute() even runs); a SECOND, unconditional command-level step-up on
    # every ordinary skill write would double-gate what is already gated,
    # which this story reserves for a real authority-widening action (none
    # of these 12 types widen anything an owl/owner does not already hold —
    # unlike `owls.build.grant`, skills carry no authority axis of their
    # own). `write` + `reversible` still gets an owl/crew requester the
    # correct `needs_approval` read-back (decide()'s own rule); only the
    # requester-kind branch changes, never the step-up-everyone-always shape.
    author_create_spec = CommandSpec(
        command_type=AUTHOR_CREATE, payload_model=SkillContentPayload,
        # Reversible: a freshly created skill's honest undo is deleting it
        # (mirrors owl_build_commands.py's create-undoes-to-retire shape).
        severity="write", reversible=True, undo_command_type=DELETE,
    )
    author_edit_spec = CommandSpec(
        command_type=AUTHOR_EDIT, payload_model=SkillContentPayload,
        # Boundaries — undo reuses the EXISTING skill.restore_version type
        # (record_skill_mutation already snapshots before/after content) —
        # never a new parallel snapshot mechanism.
        severity="write", reversible=True, undo_command_type=RESTORE_VERSION,
    )
    author_patch_spec = CommandSpec(
        command_type=AUTHOR_PATCH, payload_model=SkillContentPayload,
        severity="write", reversible=True, undo_command_type=RESTORE_VERSION,
    )
    delete_spec = CommandSpec(
        command_type=DELETE, payload_model=SkillDeletePayload,
        severity="write", reversible=True, undo_command_type=RESTORE_VERSION,
    )
    set_enabled_spec = CommandSpec(
        command_type=SET_ENABLED, payload_model=SkillSetEnabledPayload,
        # Reversible → itself: enable/disable is a trivial, always-safe
        # toggle (mirrors scheduling.pause_job/resume_job's own shape).
        severity="write", reversible=True, undo_command_type=SET_ENABLED,
    )
    synthesize_spec = CommandSpec(
        command_type=SYNTHESIZE, payload_model=SkillSynthesizePayload,
        severity="write", reversible=False,
    )
    install_spec = CommandSpec(
        command_type=INSTALL, payload_model=SkillInstallPayload,
        # Reversible: a freshly installed skill's honest undo is deleting it
        # (mirrors skill.author_create's own shape).
        severity="write", reversible=True, undo_command_type=DELETE,
    )
    reload_spec = CommandSpec(
        command_type=RELOAD_INDEX, payload_model=SkillReloadPayload,
        # Reversible → itself: an idempotent rescan, always safe to re-run.
        severity="write", reversible=True, undo_command_type=RELOAD_INDEX,
    )
    set_pinned_spec = CommandSpec(
        command_type=SET_PINNED, payload_model=SkillSetPinnedPayload,
        # Reversible → itself — mirrors skill.set_enabled's own shape.
        severity="write", reversible=True, undo_command_type=SET_PINNED,
    )
    dedupe_spec = CommandSpec(
        command_type=DEDUPE, payload_model=SkillDedupePayload,
        severity="write", reversible=False,
    )
    migrate_spec = CommandSpec(
        command_type=MIGRATE_STANDARD, payload_model=SkillMigratePayload,
        severity="write", reversible=False,
    )
    restore_version_spec = CommandSpec(
        command_type=RESTORE_VERSION, payload_model=SkillRestoreVersionPayload,
        # Reversible → itself: undo re-submits skill.restore_version with
        # the CAPTURED pre-restore hash (mirrors skill.set_enabled's shape).
        severity="write", reversible=True, undo_command_type=RESTORE_VERSION,
    )
    for spec in (
        author_create_spec, author_edit_spec, author_patch_spec, delete_spec,
        set_enabled_spec, synthesize_spec, install_spec, reload_spec,
        set_pinned_spec, dedupe_spec, migrate_spec, restore_version_spec,
    ):
        CommandSpecRegistry.register(spec)

    CommandHandlerRegistry.register(AUTHOR_CREATE, _write_content_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(AUTHOR_EDIT, _write_content_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(AUTHOR_PATCH, _write_content_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELETE, _delete_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(SET_ENABLED, _set_enabled_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(SYNTHESIZE, _synthesize_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(INSTALL, _install_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RELOAD_INDEX, _reload_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(SET_PINNED, _set_pinned_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DEDUPE, _dedupe_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(MIGRATE_STANDARD, _migrate_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RESTORE_VERSION, _restore_version_handler)  # type: ignore[arg-type]

    log.skills.info(
        "[commands] skill_commands: CommandSpecs registered",
        extra={"_fields": {"command_types": [
            AUTHOR_CREATE, AUTHOR_EDIT, AUTHOR_PATCH, DELETE, SET_ENABLED,
            SYNTHESIZE, INSTALL, RELOAD_INDEX, SET_PINNED, DEDUPE,
            MIGRATE_STANDARD, RESTORE_VERSION,
        ]}},
    )


_register()
