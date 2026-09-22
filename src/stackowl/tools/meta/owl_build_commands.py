"""``owls.build.*`` ``CommandSpec``s -- ``owl_build``'s create/edit/rename/
retire/restore/grant (Story 4.9, AD-1/AD-26).

The census (``authz/state_change_census.py``) named ``owls.build`` as one
pending 4.9 entry covering every ``owl_build`` mutation. This module SPLITS
it into one type per real action instead of keeping one coarse type for all
of create/edit/rename/retire/restore/grant -- mirroring ``scheduler/
commands.py``'s own pause/resume-vs-create/edit/delete split, so each
action's severity/reversibility/undo is declared independently rather than
forced to share one classification.

CONSOLIDATION (Design Notes in spec-4-9): ``/owl create/edit/rename/retire``
(``OwlCommand._build`` -> ``OwlBuildTool.execute()``) and the tool's own
create/edit/rename/retire actions are THE SAME real mutation -- ``/owl`` is
"the ONE owl surface" (``owl_build.py``'s own docstring). So there is no
separate ``owl.create``/``owl.edit``/... command type for the slash surface;
``commands/state_census.py``'s ``SUBCOMMAND_CENSUS`` entries for those four
sub-commands are repointed at these consolidated types instead.

PLACEMENT: here, not ``commands/spec/`` -- AD-7 restricts what
``commands/spec/`` may import (``authz/`` + ``pipeline/durable`` only,
tripwire-enforced); a SUBSYSTEM declaring its own commands is not restricted
the other way, and this module freely imports both ``commands.spec`` (to
register into) and ``commands.owls_helpers``/``owls.registry`` (the mutators
it wraps) -- mirrors ``scheduler/commands.py``/``notifications/commands.py``'s
own placement rationale.

RECEIPT SHAPE -- read-check-first / write-last (mirrors ``notifications/
commands.py``'s own Design Notes). An owl mutation touches BOTH a durable
SQLite row (``OwlStore``, via ``persist_owl``) AND an in-memory
``OwlRegistry`` in two separate calls that cannot commit atomically with a
``command_receipts`` row the way a single-table DB update can
(``scheduler_mutations.py::update_job``'s in-one-transaction shape) -- so
every handler here checks ``command_receipts`` FIRST (a found receipt means
already-attempted, no-op) and writes the receipt LAST, after the real
persist+register, exactly as ``notifications/commands.py`` does for its own
non-transactional (external) side effects.

Importing this module registers all six ``CommandSpec``s and handlers as a
side effect (mirrors ``scheduler/commands.py``'s own shape) -- imported once
from ``startup/orchestrator.py``, beside ``notifications.commands``/
``scheduler.commands``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.owls_helpers import (
    delete_owl,
    persist_owl,
    restore_owl,
    snapshot_owl,
)
from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra import presented_tools
from stackowl.infra.observability import log
from stackowl.owls.manifest import OwlAgentManifest

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.manifest import SkillSource

CREATE = "owls.build.create"
EDIT = "owls.build.edit"
RENAME = "owls.build.rename"
RETIRE = "owls.build.retire"
RESTORE = "owls.build.restore"
GRANT = "owls.build.grant"

#: The source name agent-minted owls register under -- mirrors ``owl_build.py``'s
#: own ``_SOURCE_NAME`` (kept as a separate constant here since this module must
#: not import a Tool class, per this package's own placement rationale).
_SOURCE_NAME = "agent_owls"

#: Reuses the skills audit sink's "learned" lane for provenance (DRY with
#: ``owl_build.py``'s own ``_AUDIT_SOURCE``). Typed as ``SkillSource`` (a
#: typing-only import, zero runtime cost) so ``store.audit_write``'s
#: ``Literal[...]`` param sees a narrowed value instead of a bare ``str``.
_AUDIT_SOURCE: SkillSource = "learned"

_ALREADY_ATTEMPTED_ERROR = (
    "command already attempted once — the original outcome was not "
    "recorded and is not reconfirmed by this call"
)


class OwlManifestPayload(BaseModel):
    """The shared payload shape for ``create``/``restore``/``grant``/``edit`` --
    everything already resolved (forged, clamped, consented) by the time the
    tool submits it, mirroring ``notifications/commands.py``'s ``Notification``
    precedent: the handler's only job is to persist + register the given
    manifest, never to re-derive it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: OwlAgentManifest
    #: The specific requesting owl's name (``TraceContext.get()["owl_name"]``
    #: or the secretary) — carried so the handler's audit row names WHO asked,
    #: same fidelity ``owl_build.py``'s own ``creator`` variable had.
    actor: str = Field(min_length=1)


class RenameOwlPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    display_name: str
    actor: str = Field(min_length=1)


class RetireOwlPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    #: Whether the target owl's ``origin`` is ``"builtin"`` -- the handler
    #: tombstones it (so ``register_builtin_personas`` does not resurrect it
    #: at next boot) only when true, mirroring ``owl_build.py::_retire``'s own
    #: conditional.
    is_builtin: bool = False
    actor: str = Field(min_length=1)


async def _already_executed(db: Any, command_id: str) -> bool:
    """Read-only ``command_receipts`` check -- no side effect."""
    log.tool.debug(
        "[commands] owl_build_commands._already_executed: entry",
        extra={"_fields": {"command_id": command_id}},
    )
    rows = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (command_id,),
    )
    found = bool(rows)
    log.tool.debug(
        "[commands] owl_build_commands._already_executed: exit",
        extra={"_fields": {"command_id": command_id, "already_executed": found}},
    )
    return found


async def _invalidate_prompt(owl_name: str, *, cause: str) -> None:
    """Clear an edited/renamed owl's frozen prompt + presented toolset so the
    change lands next turn -- mirrors ``owl_build.py::_invalidate_prompt``
    exactly (duplicated here rather than imported: this module must not
    import a ``Tool`` class to call an instance method on it)."""
    from stackowl.pipeline.services import get_services

    presented_tools.clear_owl(owl_name)
    store = getattr(get_services(), "session_prompt_store", None)
    if store is None:
        log.tool.error(
            "[commands] owl_build_commands._invalidate_prompt: no store wired "
            "— the change will not apply until the session rolls over",
            extra={"_fields": {"owl": owl_name, "cause": cause}},
        )
        return
    await store.invalidate_owl(owl_name=owl_name, cause=cause)


async def _audit_owl(op: str, name: str, actor: str) -> None:
    """Append a provenance audit row via the skills audit sink (best-effort) —
    mirrors ``owl_build.py::_audit`` exactly, moved here since the handler is
    now where the real mutation (and thus the honest audit instant) happens."""
    from stackowl.pipeline.services import get_services

    store = get_services().skill_store
    if store is None:
        log.tool.info(
            "[commands] owl_build_commands._audit_owl: no skill store — audit "
            "skipped (owl still persisted)",
            extra={"_fields": {"owl": name, "op": op}},
        )
        return
    try:
        await store.audit_write(
            skill_name=name, source=_AUDIT_SOURCE, op=op, actor=actor,
            details={"kind": "agent_owl", "created_by": actor},
        )
    except Exception as exc:  # B5 — never fail the command on an audit hiccup
        log.tool.warning(
            "[commands] owl_build_commands._audit_owl: audit_write failed — "
            "owl persisted, audit pending",
            exc_info=exc, extra={"_fields": {"owl": name, "op": op}},
        )


async def _reconcile_schedules() -> None:
    """Re-project owl schedules after a create/edit/retire/restore -- mirrors
    ``owl_build.py::_reconcile_schedules``. Fail-safe: never raises."""
    from stackowl.pipeline.services import get_services

    svc = get_services()
    db = svc.db_pool
    registry = svc.owl_registry
    if db is None or registry is None:
        return
    try:
        from stackowl.scheduler.owl_lifecycle import reconcile_owl_schedules

        settings = svc.settings
        tz = settings.system.timezone if settings is not None else "UTC"
        await reconcile_owl_schedules(registry, db, tz=tz or "UTC", settings=settings)
    except Exception as exc:  # B5 — never fail the command on a reconcile hiccup
        log.tool.error(
            "[commands] owl_build_commands._reconcile_schedules: failed — owl "
            "change persisted",
            exc_info=exc,
        )


# =============================================================================
# owls.build.create
# =============================================================================


async def _create_handler(
    payload: OwlManifestPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        log.tool.warning(
            "[commands] owl_build_commands._create_handler: no registry/db",
            extra={"_fields": {"owl": payload.manifest.name}},
        )
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    manifest = payload.manifest
    if await _already_executed(db, context.command_id):
        log.tool.warning(
            "[commands] owl_build_commands._create_handler: command already "
            "attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": manifest.name, "already_done": True},
        )

    snapshot = await snapshot_owl(manifest.name)
    try:
        await persist_owl(manifest)
        registry.register(manifest, source_name=_SOURCE_NAME)
    except Exception as exc:  # B5 — no-hidden-errors, atomic rollback
        log.tool.error(
            "[commands] owl_build_commands._create_handler: persist/register "
            "failed — rolling back",
            exc_info=exc, extra={"_fields": {"owl": manifest.name}},
        )
        await restore_owl(manifest.name, snapshot)
        return CommandOutcome(
            success=False, error=f"failed to persist owl '{manifest.name}': {exc}",
        )

    # Capture authored DNA baseline (fail-safe — never fails the create).
    try:
        from stackowl.owls.dna_authored import capture_one_authored

        await capture_one_authored(db, manifest.name, manifest.dna)
    except Exception as exc:  # B5
        log.tool.warning(
            "[commands] owl_build_commands._create_handler: DNA baseline "
            "capture failed",
            exc_info=exc, extra={"_fields": {"owl": manifest.name}},
        )
    await _reconcile_schedules()
    await _audit_owl("create", manifest.name, payload.actor)

    # Undo of a create is retiring the same owl (RetireOwlPayload's whole
    # shape is {name, is_builtin, actor}) — captured explicitly rather than
    # left to request_undo's fallback, which would otherwise resubmit THIS
    # command's own OwlManifestPayload ({manifest, actor}) straight into
    # RetireOwlPayload's extra="forbid" model and raise a ValidationError
    # (review finding, 2026-09-22 pass).
    undo_payload = json.dumps({
        "name": manifest.name, "is_builtin": manifest.origin == "builtin",
        "actor": payload.actor,
    })
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"owl": manifest.name})


# =============================================================================
# owls.build.edit
# =============================================================================


async def _edit_handler(
    payload: OwlManifestPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    manifest = payload.manifest
    if await _already_executed(db, context.command_id):
        log.tool.warning(
            "[commands] owl_build_commands._edit_handler: command already "
            "attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": manifest.name, "already_done": True},
        )

    prior = await snapshot_owl(manifest.name)
    try:
        await persist_owl(manifest)
        registry.replace(manifest)
        await _invalidate_prompt(manifest.name, cause="owl_build_edit")
    except Exception as exc:  # B5 — no-hidden-errors, roll back
        log.tool.error(
            "[commands] owl_build_commands._edit_handler: persist/register "
            "failed — rolling back",
            exc_info=exc, extra={"_fields": {"owl": manifest.name}},
        )
        await restore_owl(manifest.name, prior)
        return CommandOutcome(
            success=False, error=f"failed to edit owl '{manifest.name}': {exc}",
        )
    await _reconcile_schedules()
    await _audit_owl("edit", manifest.name, payload.actor)

    # Story 4.7's edit-reversible-to-itself shape (undo_payload = the CAPTURED
    # prior state) — here the "prior state" is the whole prior manifest, since
    # owls.build.edit's own payload IS the whole target manifest.
    undo_payload = (
        json.dumps({"manifest": prior.model_dump(mode="json"), "actor": payload.actor})
        if prior is not None else None
    )
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"owl": manifest.name})


# =============================================================================
# owls.build.rename
# =============================================================================


async def _rename_handler(
    payload: RenameOwlPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    if await _already_executed(db, context.command_id):
        log.tool.warning(
            "[commands] owl_build_commands._rename_handler: command already "
            "attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": payload.name, "already_done": True},
        )
    try:
        current = registry.get(payload.name)
    except Exception:  # OwlNotFoundError — the not-found path is expected
        return CommandOutcome(success=False, error=f"no owl named '{payload.name}' to rename.")

    prior_display = current.display_name or ""
    rebuilt = current.model_copy(update={"display_name": payload.display_name})
    snapshot = await snapshot_owl(rebuilt.name)
    try:
        await persist_owl(rebuilt)
        registry.replace(rebuilt)
        await _invalidate_prompt(rebuilt.name, cause="owl_build_edit")
    except Exception as exc:  # B5 — no-hidden-errors, roll back
        log.tool.error(
            "[commands] owl_build_commands._rename_handler: persist/register "
            "failed — rolling back",
            exc_info=exc, extra={"_fields": {"owl": rebuilt.name}},
        )
        await restore_owl(rebuilt.name, snapshot)
        return CommandOutcome(
            success=False, error=f"failed to rename owl '{rebuilt.name}': {exc}",
        )

    await _audit_owl("rename", rebuilt.name, payload.actor)
    undo_payload = json.dumps({
        "name": rebuilt.name, "display_name": prior_display, "actor": payload.actor,
    })
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(
        success=True, result={"owl": rebuilt.name, "display_name": payload.display_name},
    )


# =============================================================================
# owls.build.retire / owls.build.restore
# =============================================================================


async def _retire_handler(
    payload: RetireOwlPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.commands.owls_command import OwlsCommand
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    if await _already_executed(db, context.command_id):
        log.tool.warning(
            "[commands] owl_build_commands._retire_handler: command already "
            "attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": payload.name, "already_done": True},
        )
    snapshot = await snapshot_owl(payload.name)
    if snapshot is None:
        return CommandOutcome(success=False, error=f"no owl named '{payload.name}' to retire.")

    try:
        await delete_owl(payload.name)  # durable first — the sqlite home
        if payload.is_builtin:
            OwlsCommand()._add_retired_builtin(payload.name)  # noqa: SLF001
        registry.deregister(payload.name)
    except Exception as exc:  # B5 — no-hidden-errors, roll back
        log.tool.error(
            "[commands] owl_build_commands._retire_handler: retire failed — "
            "rolling back",
            exc_info=exc, extra={"_fields": {"owl": payload.name}},
        )
        await restore_owl(payload.name, snapshot)
        return CommandOutcome(
            success=False, error=f"failed to retire owl '{payload.name}': {exc}",
        )
    await _reconcile_schedules()
    await _audit_owl("retire", payload.name, payload.actor)

    # The retire payload is just {name} — it cannot recreate a manifest, so
    # the undo (owls.build.restore) needs the FULL prior manifest, captured
    # here before the delete (Boundaries: "retire's own payload cannot
    # recreate a manifest").
    undo_payload = json.dumps({
        "manifest": snapshot.model_dump(mode="json"), "actor": payload.actor,
    })
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"owl": payload.name})


async def _restore_handler(
    payload: OwlManifestPayload, context: CommandContext,
) -> CommandOutcome:
    """Internal-only — reachable only as ``owls.build.retire``'s undo target
    (never submitted directly by the tool). Recreates the retired owl from
    the manifest snapshot ``owls.build.retire`` captured."""
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    manifest = payload.manifest
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": manifest.name, "already_done": True},
        )
    try:
        await persist_owl(manifest)
        registry.register(manifest, source_name=_SOURCE_NAME)
    except Exception as exc:  # B5
        log.tool.error(
            "[commands] owl_build_commands._restore_handler: restore failed",
            exc_info=exc, extra={"_fields": {"owl": manifest.name}},
        )
        return CommandOutcome(
            success=False, error=f"failed to restore owl '{manifest.name}': {exc}",
        )
    await _reconcile_schedules()
    await _audit_owl("restore", manifest.name, payload.actor)

    # A restore is itself reversible — undoing it means re-retiring the same
    # owl, so the undo_payload matches RetireOwlPayload's own shape rather
    # than re-submitting this restore's own (RetireOwlPayload has no
    # `manifest` field to resubmit against).
    undo_payload = json.dumps({"name": manifest.name, "actor": payload.actor})
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"owl": manifest.name})


# =============================================================================
# owls.build.grant
# =============================================================================


async def _grant_handler(
    payload: OwlManifestPayload, context: CommandContext,
) -> CommandOutcome:
    """Persist + register the already-widened manifest ``owl_build._grant``
    built (consent for the widening already happened in the tool, gated
    ``authority_widening`` — see that method's own docstring). Never given an
    undo (Boundaries: no revoke mutator exists)."""
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="owl registry/store unavailable")
    manifest = payload.manifest
    if await _already_executed(db, context.command_id):
        log.tool.warning(
            "[commands] owl_build_commands._grant_handler: command already "
            "attempted once — outcome not reconfirmed by this call",
            extra={"_fields": {"command_id": context.command_id}},
        )
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"owl": manifest.name, "already_done": True},
        )
    snapshot = await snapshot_owl(manifest.name)
    try:
        await persist_owl(manifest)
        registry.replace(manifest)
        # Review finding (2026-09-22 pass): a grant widens exactly the
        # tools/capability_profile axis _invalidate_prompt exists to drop —
        # without this, the owl keeps being handed its pre-grant toolset and
        # frozen prompt for the rest of the session, reproducing the
        # "agents forget granted accesses" defect this handler exists to fix.
        await _invalidate_prompt(manifest.name, cause="owl_build_grant")
    except Exception as exc:  # B5 — no-hidden-errors, roll back
        log.tool.error(
            "[commands] owl_build_commands._grant_handler: persist/register "
            "failed — rolling back",
            exc_info=exc, extra={"_fields": {"owl": manifest.name}},
        )
        await restore_owl(manifest.name, snapshot)
        return CommandOutcome(
            success=False, error=f"failed to grant tools to '{manifest.name}': {exc}",
        )
    await _audit_owl("grant", manifest.name, payload.actor)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(success=True, result={"owl": manifest.name})


def _register() -> None:
    create_spec = CommandSpec(
        command_type=CREATE,
        payload_model=OwlManifestPayload,
        # `write`, not `consequential` — Design Notes: ONLY `owls.build.grant`
        # (the authority-widening action) gets the unconditional step-up.
        # create/edit/rename/retire stay `write` so the owner's own reversible
        # order still runs at once after the tool's OWN ConsequentialActionGate
        # consent already ran (the "double-gate" this story keeps is grant's
        # alone, mirroring send_message's kept tool-level gate — not a SECOND
        # step-up on every ordinary create).
        severity="write",
        # Reversible: a created owl's honest undo is retiring it — declared
        # here, never invoked outside request_undo (Story 4.5's own machinery).
        reversible=True,
        undo_command_type=RETIRE,
    )
    edit_spec = CommandSpec(
        command_type=EDIT,
        payload_model=OwlManifestPayload,
        severity="write",
        # Reversible → itself: undo re-submits owls.build.edit with the
        # CAPTURED prior manifest — mirrors scheduling.edit_job's own shape.
        reversible=True,
        undo_command_type=EDIT,
    )
    rename_spec = CommandSpec(
        command_type=RENAME,
        payload_model=RenameOwlPayload,
        severity="write",
        reversible=True,
        undo_command_type=RENAME,
    )
    retire_spec = CommandSpec(
        command_type=RETIRE,
        payload_model=RetireOwlPayload,
        severity="write",
        reversible=True,
        undo_command_type=RESTORE,
    )
    restore_spec = CommandSpec(
        command_type=RESTORE,
        payload_model=OwlManifestPayload,
        severity="write",
        # Reversible: undoing a restore just re-retires the same owl, closing
        # the create<->retire<->restore loop symmetrically. Declaring this
        # irreversible would force every retire-undo through a SECOND
        # step-up (irreversible => needs_step_up unconditionally) even though
        # restoring a previously-sanctioned owl carries no new authority —
        # the exact failure this story's AC3 ("undo restores it") tests for.
        reversible=True,
        undo_command_type=RETIRE,
    )
    grant_spec = CommandSpec(
        command_type=GRANT,
        payload_model=OwlManifestPayload,
        severity="consequential",
        # Boundaries: never given an undo — no revoke mutator exists.
        reversible=False,
    )
    CommandSpecRegistry.register(create_spec)
    CommandSpecRegistry.register(edit_spec)
    CommandSpecRegistry.register(rename_spec)
    CommandSpecRegistry.register(retire_spec)
    CommandSpecRegistry.register(restore_spec)
    CommandSpecRegistry.register(grant_spec)
    CommandHandlerRegistry.register(CREATE, _create_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(EDIT, _edit_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RENAME, _rename_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RETIRE, _retire_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(RESTORE, _restore_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(GRANT, _grant_handler)  # type: ignore[arg-type]

    log.tool.info(
        "[commands] owl_build_commands: CommandSpecs registered",
        extra={"_fields": {"command_types": [CREATE, EDIT, RENAME, RETIRE, RESTORE, GRANT]}},
    )


_register()
