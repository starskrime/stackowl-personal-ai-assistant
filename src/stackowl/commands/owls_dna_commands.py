"""``owl.{reset_dna,dna_restore,cancel_objective,merge_objective}``
``CommandSpec``s (Story 4.9, AD-1/AD-26).

Reachable only via ``/owl`` — ``/owls`` is unregistered (``commands/
manifest.py:74-77``) — so ``OwlsCommand._reset_dna``/``._dna_restore``/
``._objective_cancel``/``._objective_merge`` (inherited, unchanged, by
``OwlCommand``) are this module's only live callers.

All four are ``severity="consequential"``, ``reversible=False`` (mirrors the
existing ``SUBCOMMAND_CENSUS`` classification for ``owl.reset-dna``/
``owl.dna-restore``/``owl.objective-cancel``/``owl.objective-merge`` — none
of them has a natural single-step undo: DNA reset/restore already IS a
restore-to-a-prior-state action, and abandoning/merging an objective is a
one-way state transition with its own git side effects).

PLACEMENT: here, not ``commands/spec/`` — AD-7's import-boundary rationale is
identical to ``scheduler/commands.py``'s/``owl_build_commands.py``'s own.

Importing this module registers all four ``CommandSpec``s and handlers as a
side effect — imported once from ``startup/orchestrator.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

RESET_DNA = "owl.reset_dna"
DNA_RESTORE = "owl.dna_restore"
CANCEL_OBJECTIVE = "owl.cancel_objective"
MERGE_OBJECTIVE = "owl.merge_objective"

_ALREADY_ATTEMPTED_ERROR = (
    "command already attempted once — the original outcome was not "
    "recorded and is not reconfirmed by this call"
)


class OwlNamePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)


class DnaRestorePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)


class ObjectivePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    objective_id: str = Field(min_length=1)


async def _already_executed(db: Any, command_id: str) -> bool:
    """Read-only ``command_receipts`` check -- no side effect."""
    log.gateway.debug(
        "[commands] owls_dna_commands._already_executed: entry",
        extra={"_fields": {"command_id": command_id}},
    )
    rows = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (command_id,),
    )
    found = bool(rows)
    log.gateway.debug(
        "[commands] owls_dna_commands._already_executed: exit",
        extra={"_fields": {"command_id": command_id, "already_executed": found}},
    )
    return found


# =============================================================================
# owl.reset_dna
# =============================================================================


async def _reset_dna_handler(
    payload: OwlNamePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="DNA store unavailable.")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"name": payload.name, "already_done": True},
        )
    from stackowl.owls.directive_latch import DIRECTIVE_LATCH
    from stackowl.owls.dna_authored import read_authored_dna
    from stackowl.owls.dna_hydrator import apply_dna_overlay
    from stackowl.owls.dna_storage import upsert_owl_dna

    authored = await read_authored_dna(db, payload.name)
    if authored is None:
        return CommandOutcome(
            success=False,
            error=f"no authored baseline recorded for '{payload.name}' — nothing to reset to.",
        )
    await upsert_owl_dna(db, payload.name, authored, table="owl_dna")
    apply_dna_overlay(registry, payload.name, authored)
    DIRECTIVE_LATCH.reset_owl(payload.name)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(success=True, result={"name": payload.name})


# =============================================================================
# owl.dna_restore
# =============================================================================


async def _dna_restore_handler(
    payload: DnaRestorePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    svc = get_services()
    registry = svc.owl_registry
    db = svc.db_pool
    if registry is None or db is None:
        return CommandOutcome(success=False, error="DNA store unavailable.")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"name": payload.name, "already_done": True},
        )
    from stackowl.owls.directive_latch import DIRECTIVE_LATCH
    from stackowl.owls.dna import OwlDNA
    from stackowl.owls.dna_hydrator import apply_dna_overlay
    from stackowl.owls.dna_storage import upsert_owl_dna
    from stackowl.owls.learning_artifact_store import LearningArtifactStore

    store = LearningArtifactStore(db)
    try:
        restore_payload = await store.restore("dna", payload.name, payload.checkpoint_id)
    except Exception as exc:  # ManifestValidationError (or similar) — structured refusal
        log.gateway.warning(
            "[commands] owls_dna_commands._dna_restore_handler: restore failed",
            exc_info=exc,
            extra={"_fields": {"name": payload.name, "checkpoint_id": payload.checkpoint_id}},
        )
        return CommandOutcome(success=False, error=str(exc))
    restored_dna = OwlDNA.model_validate(restore_payload)
    await upsert_owl_dna(db, payload.name, restored_dna, table="owl_dna")
    apply_dna_overlay(registry, payload.name, restored_dna)
    DIRECTIVE_LATCH.reset_owl(payload.name)

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(
        success=True, result={"name": payload.name, "checkpoint_id": payload.checkpoint_id},
    )


# =============================================================================
# owl.cancel_objective
# =============================================================================


async def _cancel_objective_handler(
    payload: ObjectivePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.objectives.store import ObjectiveNotFoundError, ObjectiveStore
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        return CommandOutcome(success=False, error="no database wired — cannot manage objectives.")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"objective_id": payload.objective_id, "already_done": True},
        )
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    try:
        objective = await store.get(payload.objective_id)
    except ObjectiveNotFoundError:
        return CommandOutcome(success=False, error=f"no such objective: {payload.objective_id!r}")

    if objective.repo:
        from stackowl.tools.system.git_tool import GitTool

        git = GitTool()
        for sg in await store.list_subgoals(payload.objective_id):
            if sg.worktree_path:
                await git(
                    operation="worktree_remove", repo=objective.repo,
                    path=sg.worktree_path, force=True,
                )
    await store.update_status(payload.objective_id, "abandoned")
    await store.append_event(payload.objective_id, "abandoned", "cancelled by owner")

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(success=True, result={"objective_id": payload.objective_id})


# =============================================================================
# owl.merge_objective
# =============================================================================


async def _merge_objective_handler(
    payload: ObjectivePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.objectives.store import ObjectiveNotFoundError, ObjectiveStore
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        return CommandOutcome(success=False, error="no database wired — cannot manage objectives.")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"objective_id": payload.objective_id, "already_done": True},
        )
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    try:
        objective = await store.get(payload.objective_id)
    except ObjectiveNotFoundError:
        return CommandOutcome(success=False, error=f"no such objective: {payload.objective_id!r}")

    if not objective.repo or not objective.integration_branch:
        return CommandOutcome(
            success=False, error=f"'{payload.objective_id}' is not an epic (no repo/integration branch)",
        )
    if objective.status != "blocked":
        return CommandOutcome(
            success=False,
            error=f"'{payload.objective_id}' is not ready to merge (status: {objective.status})",
        )

    subgoals = await store.list_subgoals(payload.objective_id)
    done = [sg for sg in subgoals if sg.status == "done"]
    if not done:
        return CommandOutcome(
            success=False, error=f"'{payload.objective_id}' has no completed stories to merge",
        )

    from stackowl.tools.system.git_tool import GitTool
    from stackowl.tools.system.shell import run_argv

    checkout = await run_argv(
        ["git", "checkout", objective.base_branch or ""],
        tool_name="git", workdir=objective.repo, intent="write",
    )
    if not checkout.success:
        return CommandOutcome(
            success=False, error=f"could not check out '{objective.base_branch}': {checkout.error}",
        )
    merge = await run_argv(
        ["git", "merge", "--no-ff", objective.integration_branch],
        tool_name="git", workdir=objective.repo, intent="write",
    )
    if not merge.success:
        return CommandOutcome(
            success=False,
            error=f"final merge failed (left blocked for manual resolution): {merge.error}",
        )

    git = GitTool()
    done_ids = {sg.subgoal_id for sg in done}
    for sg in subgoals:
        if sg.subgoal_id not in done_ids and sg.worktree_path:
            await git(operation="worktree_remove", repo=objective.repo, path=sg.worktree_path, force=True)

    await store.update_status(payload.objective_id, "done")
    await store.append_event(
        payload.objective_id, "epic_merged",
        f"{len(done)}/{len(subgoals)} stories merged into {objective.base_branch}",
    )

    async with db.transaction() as conn:
        await record_command_execution(conn, context.command_id, context.command_type)
    return CommandOutcome(
        success=True,
        result={
            "objective_id": payload.objective_id, "merged": len(done), "total": len(subgoals),
            "base_branch": objective.base_branch,
        },
    )


def _register() -> None:
    reset_dna_spec = CommandSpec(
        command_type=RESET_DNA, payload_model=OwlNamePayload,
        severity="consequential", reversible=False,
    )
    dna_restore_spec = CommandSpec(
        command_type=DNA_RESTORE, payload_model=DnaRestorePayload,
        severity="consequential", reversible=False,
    )
    cancel_objective_spec = CommandSpec(
        command_type=CANCEL_OBJECTIVE, payload_model=ObjectivePayload,
        severity="consequential", reversible=False,
    )
    merge_objective_spec = CommandSpec(
        command_type=MERGE_OBJECTIVE, payload_model=ObjectivePayload,
        severity="consequential", reversible=False,
    )
    CommandSpecRegistry.register(reset_dna_spec)
    CommandSpecRegistry.register(dna_restore_spec)
    CommandSpecRegistry.register(cancel_objective_spec)
    CommandSpecRegistry.register(merge_objective_spec)
    CommandHandlerRegistry.register(RESET_DNA, _reset_dna_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DNA_RESTORE, _dna_restore_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(CANCEL_OBJECTIVE, _cancel_objective_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(MERGE_OBJECTIVE, _merge_objective_handler)  # type: ignore[arg-type]

    log.gateway.info(
        "[commands] owls_dna_commands: CommandSpecs registered",
        extra={"_fields": {
            "command_types": [RESET_DNA, DNA_RESTORE, CANCEL_OBJECTIVE, MERGE_OBJECTIVE],
        }},
    )


_register()
