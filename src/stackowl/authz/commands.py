"""``authority.grant``/``authority.revoke`` -- the two ``CommandSpec``s that
are the ONLY way a ``standing_authority`` row is ever written (Story 4.6,
FR32/FR37, AD-1/AD-26/AD-27).

PLACEMENT: here, inside ``authz/``, not ``commands/spec/`` -- AD-7 restricts
what ``commands/spec/`` may import (``authz/`` + ``pipeline/durable`` only,
tripwire-enforced); a package DECLARING its own commands is not restricted
the other way, and this module freely imports both ``commands.spec`` (to
register into) and ``authz.standing_authority`` (the mutator it wraps) --
mirrors ``scheduler/commands.py``'s own placement rationale exactly, just for
``authz/``'s own domain rather than a subsystem's.

Both specs are declared ``severity="consequential"`` -- the load-bearing
choice this story's whole "never self-granted, never through voice" argument
rests on (``action_policy.py``'s own module docstring: CONSEQUENTIAL is
checked FIRST, unconditional on requester kind, before this module ever
existed). Each names the OTHER as its ``undo_command_type`` (granting is
undone by revoking and vice versa), sharing one payload shape
(:class:`AuthorityScopePayload`) so Story 4.5's undo path re-submits the
exact same ``{scope_kind, scope_id, command_type}`` unmodified.

Importing this module registers both ``CommandSpec``s and their handlers as
a side effect (mirrors ``scheduler/commands.py``'s own shape) -- imported
once from ``startup/orchestrator.py``, beside ``stackowl.scheduler.commands``.

Handlers resolve their own db pool via ``get_services()`` rather than taking
one as a parameter -- ``CommandHandler``'s shape (``command_spec.py``) is
fixed at ``(payload, context) -> CommandOutcome`` for every registrant, the
same reason ``scheduler/commands.py``'s own handlers do this.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.authz import standing_authority
from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log

AUTHORITY_GRANT = "authority.grant"
AUTHORITY_REVOKE = "authority.revoke"

#: AD-4's own bound ("attrs hold ... bounded labels of at most 64
#: characters") applies one hop downstream too: `AuthorityGrantedAttrs`/
#: `AuthorityRevokedAttrs` (journal/authority_events.py) enforce
#: `max_length=64` on these same two fields, so an oversized payload must be
#: refused HERE, at payload validation, rather than raising an unhandled
#: `ValidationError` mid-transaction inside `standing_authority.grant`/
#: `.revoke`.
_MAX_LABEL_LEN = 64


class AuthorityScopePayload(BaseModel):
    """The one payload shape both commands share (Story 4.5's own
    payload-equality undo target check needs the grant/revoke pair to agree
    byte-for-byte on what they name)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Reuses ``authz.standing_authority.ScopeKind`` directly (never a
    #: second, hand-typed ``Literal["job"]``) so the two can never silently
    #: drift apart.
    scope_kind: standing_authority.ScopeKind
    scope_id: str = Field(min_length=1, max_length=_MAX_LABEL_LEN)
    command_type: str = Field(min_length=1, max_length=_MAX_LABEL_LEN)


async def _grant_handler(
    payload: AuthorityScopePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.engine.warning(
            "[commands] authz.commands._grant_handler: no db pool",
            extra={"_fields": {"scope_id": payload.scope_id}},
        )
        return CommandOutcome(
            success=False, error="standing authority unavailable (no database configured)",
        )
    record = await standing_authority.grant(
        db, scope_kind=payload.scope_kind, scope_id=payload.scope_id,
        command_type=payload.command_type, granted_by=context.requester_kind,
    )
    return CommandOutcome(success=True, result={"id": record.id})


async def _revoke_handler(
    payload: AuthorityScopePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.engine.warning(
            "[commands] authz.commands._revoke_handler: no db pool",
            extra={"_fields": {"scope_id": payload.scope_id}},
        )
        return CommandOutcome(
            success=False, error="standing authority unavailable (no database configured)",
        )
    record = await standing_authority.revoke(
        db, scope_kind=payload.scope_kind, scope_id=payload.scope_id,
        command_type=payload.command_type, revoked_by=context.requester_kind,
    )
    if record is None:
        return CommandOutcome(
            success=True,
            result={"scope_id": payload.scope_id, "revoked": False},
        )
    return CommandOutcome(success=True, result={"id": record.id, "revoked": True})


def _register() -> None:
    grant_spec = CommandSpec(
        command_type=AUTHORITY_GRANT,
        payload_model=AuthorityScopePayload,
        severity="consequential",
        reversible=True,
        undo_command_type=AUTHORITY_REVOKE,
    )
    revoke_spec = CommandSpec(
        command_type=AUTHORITY_REVOKE,
        payload_model=AuthorityScopePayload,
        severity="consequential",
        reversible=True,
        undo_command_type=AUTHORITY_GRANT,
    )
    CommandSpecRegistry.register(grant_spec)
    CommandSpecRegistry.register(revoke_spec)
    CommandHandlerRegistry.register(AUTHORITY_GRANT, _grant_handler)  # type: ignore[arg-type]
    CommandHandlerRegistry.register(AUTHORITY_REVOKE, _revoke_handler)  # type: ignore[arg-type]
    log.engine.info(
        "[commands] authz.commands: standing-authority CommandSpecs registered",
        extra={"_fields": {"command_types": [AUTHORITY_GRANT, AUTHORITY_REVOKE]}},
    )


_register()
