"""``owls.build_tool.{create,delete}`` ``CommandSpec``s -- ``tool_build``'s
create/delete (Story 4.9, AD-1/AD-26).

The census named ``owls.build_tool`` as one pending 4.9 entry covering the
whole tool; split into ``create``/``delete`` (mirrors ``owl_build_commands.
py``'s own per-action split) — ``list`` is read-only and was never a state
change.

PLACEMENT: here, not ``commands/spec/`` — AD-7's import-boundary rationale is
identical to ``owl_build_commands.py``'s own.

RECEIPT SHAPE: read-check-first / write-last (mirrors ``owl_build_commands.
py``'s own Design Notes) — a learned tool's spec file write and its live
``ToolRegistry`` registration are two separate, non-transactional calls.

Importing this module registers both ``CommandSpec``s and handlers as a side
effect — imported once from ``startup/orchestrator.py``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log
from stackowl.tools.meta.tool_spec import LearnedToolSpec

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.skills.manifest import SkillSource

CREATE = "owls.build_tool.create"
DELETE = "owls.build_tool.delete"

#: Source name under which learned tools register — mirrors ``tool_build.py``'s
#: own ``_SOURCE_NAME``.
_SOURCE_NAME = "learned_tools"
#: Typed as ``SkillSource`` (typing-only import) so ``store.audit_write``'s
#: ``Literal[...]`` param sees a narrowed value instead of a bare ``str``.
_AUDIT_SOURCE: SkillSource = "learned"

_ALREADY_ATTEMPTED_ERROR = (
    "command already attempted once — the original outcome was not "
    "recorded and is not reconfirmed by this call"
)


class DeleteToolPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)


async def _already_executed(db: Any, command_id: str) -> bool:
    """Read-only ``command_receipts`` check -- no side effect."""
    log.tool.debug(
        "[commands] tool_build_commands._already_executed: entry",
        extra={"_fields": {"command_id": command_id}},
    )
    rows = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (command_id,),
    )
    found = bool(rows)
    log.tool.debug(
        "[commands] tool_build_commands._already_executed: exit",
        extra={"_fields": {"command_id": command_id, "already_executed": found}},
    )
    return found


async def _audit_tool(op: str, name: str, *, snapshot: dict[str, str]) -> None:
    from stackowl.pipeline.services import get_services

    store = get_services().skill_store
    if store is None:
        log.tool.info(
            "[commands] tool_build_commands._audit_tool: no skill store — audit skipped",
            extra={"_fields": {"tool": name, "op": op}},
        )
        return
    try:
        await store.audit_write(
            skill_name=name, source=_AUDIT_SOURCE, op=op, actor="agent_self:tool_build",
            details={"kind": "learned_tool"}, snapshot=snapshot,
        )
    except Exception as exc:  # B5 — never fail the command on an audit hiccup
        log.tool.warning(
            "[commands] tool_build_commands._audit_tool: audit_write failed",
            exc_info=exc, extra={"_fields": {"tool": name, "op": op}},
        )


async def _create_handler(
    payload: LearnedToolSpec, context: CommandContext,
) -> CommandOutcome:
    from stackowl.paths import StackowlHome
    from stackowl.pipeline.services import get_services
    from stackowl.tools.meta.learned_shell_tool import LearnedShellTool

    db = get_services().db_pool
    if db is None:
        return CommandOutcome(success=False, error="tool registry/store unavailable")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"tool": payload.name, "already_done": True},
        )

    spec_path = StackowlHome.learned_tools_dir() / f"{payload.name}.json"
    spec_text = payload.model_dump_json(indent=2)
    try:
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_text, encoding="utf-8")
    except OSError as exc:
        return CommandOutcome(
            success=False, error=f"could not persist tool '{payload.name}': {exc}",
        )
    await _audit_tool("create", payload.name, snapshot={f"{payload.name}.json": spec_text})

    registry = get_services().tool_registry
    if registry is not None:
        try:
            registry.register(LearnedShellTool(payload), source_name=_SOURCE_NAME)
        except Exception as exc:  # B5 — roll back the persisted file
            log.tool.error(
                "[commands] tool_build_commands._create_handler: live registration "
                "failed — rolling back file",
                exc_info=exc, extra={"_fields": {"tool": payload.name}},
            )
            spec_path.unlink(missing_ok=True)
            await _audit_tool("delete", payload.name, snapshot={f"{payload.name}.json": spec_text})
            return CommandOutcome(
                success=False,
                error=f"could not register tool '{payload.name}' ({exc}); the "
                      "persisted spec was rolled back.",
            )

    # Undo of a create is deleting the same tool — DeleteToolPayload's whole
    # shape is {name}, captured explicitly rather than left to request_undo's
    # fallback, which would otherwise resubmit THIS command's own
    # LearnedToolSpec payload straight into DeleteToolPayload's
    # extra="forbid" model and raise a ValidationError (review finding,
    # 2026-09-22 pass).
    undo_payload = json.dumps({"name": payload.name})
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"tool": payload.name})


async def _delete_handler(
    payload: DeleteToolPayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.paths import StackowlHome
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        return CommandOutcome(success=False, error="tool registry/store unavailable")
    if await _already_executed(db, context.command_id):
        return CommandOutcome(
            success=False, error=_ALREADY_ATTEMPTED_ERROR,
            result={"tool": payload.name, "already_done": True},
        )

    spec_path = StackowlHome.learned_tools_dir() / f"{payload.name}.json"
    if not spec_path.exists():
        return CommandOutcome(success=False, error=f"no learned tool named '{payload.name}' to delete.")
    try:
        spec_text = spec_path.read_text(encoding="utf-8")
    except OSError:
        spec_text = ""
    try:
        spec_path.unlink(missing_ok=True)
    except OSError as exc:
        return CommandOutcome(success=False, error=f"could not delete learned tool '{payload.name}': {exc}")

    registry = get_services().tool_registry
    if registry is not None:
        tool = registry.get(payload.name)
        if tool is not None:
            try:
                if not registry.unregister(payload.name):
                    log.tool.warning(
                        "[commands] tool_build_commands._delete_handler: "
                        "unregister was a no-op — tool already absent",
                        extra={"_fields": {"tool": payload.name}},
                    )
            except Exception as exc:  # B5 — never raise on cleanup
                log.tool.warning(
                    "[commands] tool_build_commands._delete_handler: registry drop "
                    "failed — file already removed",
                    exc_info=exc, extra={"_fields": {"tool": payload.name}},
                )
    await _audit_tool("delete", payload.name, snapshot={f"{payload.name}.json": spec_text})

    # Boundaries/Code Map — captures the spec file content as undo_payload
    # BEFORE unlinking (undo=create): a delete's own payload ({name}) cannot
    # recreate the tool, so the undo re-submits owls.build_tool.create with
    # the full spec dict this delete just removed.
    undo_payload = spec_text if spec_text else None
    async with db.transaction() as conn:
        await record_command_execution(
            conn, context.command_id, context.command_type, undo_payload,
        )
    return CommandOutcome(success=True, result={"tool": payload.name})


def _register() -> None:
    create_spec = CommandSpec(
        command_type=CREATE, payload_model=LearnedToolSpec,
        severity="write",
        # Reversible: a learned tool's honest undo is deleting it — mirrors
        # tool_build.py's own `_consent_or_refuse` reversible=True reasoning.
        reversible=True, undo_command_type=DELETE,
    )
    delete_spec = CommandSpec(
        command_type=DELETE, payload_model=DeleteToolPayload,
        severity="write", reversible=True, undo_command_type=CREATE,
    )
    CommandSpecRegistry.register(create_spec)
    CommandSpecRegistry.register(delete_spec)
    CommandHandlerRegistry.register(CREATE, _create_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(DELETE, _delete_handler)  # type: ignore[arg-type]

    log.tool.info(
        "[commands] tool_build_commands: CommandSpecs registered",
        extra={"_fields": {"command_types": [CREATE, DELETE]}},
    )


_register()
