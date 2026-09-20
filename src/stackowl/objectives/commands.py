"""``scheduling.set_objective`` -- the ``CommandSpec`` an EPIC or plain
objective's initial persistence submits through (Story 4.7, AD-1/AD-26).

PLACEMENT: here, inside ``objectives/``, not ``commands/spec/`` -- AD-7
restricts what ``commands/spec/`` may import (``authz/`` + ``pipeline/durable``
only, tripwire-enforced); a SUBSYSTEM declaring its own commands is not
restricted the other way, and this module freely imports both
``commands.spec`` (to register into) and ``objectives.store``/``model`` (the
mutator it wraps) -- mirrors ``scheduler/commands.py``'s own placement
rationale exactly, just for the ``objectives`` domain.

AD-26 ("no model call in a handler"): decomposition (``ObjectiveDecomposer``)
and ``validate_graph`` run in ``tools/scheduling/objective_tool.py::execute``,
BEFORE ``submit_command`` -- this handler receives already-decomposed
``SubgoalSpec``s and does ONLY deterministic persistence.

Importing this module registers the ``CommandSpec`` and its handler as a
side effect (mirrors ``scheduler/commands.py``'s own shape) -- imported once
from ``startup/orchestrator.py``, beside ``stackowl.scheduler.commands``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.spec.command_spec import CommandSpec
from stackowl.commands.spec.context import CommandContext, CommandOutcome
from stackowl.commands.spec.handlers import CommandHandlerRegistry
from stackowl.commands.spec.idempotency import record_command_execution
from stackowl.commands.spec.registry import CommandSpecRegistry
from stackowl.infra.observability import log
from stackowl.journal import ActorKind, JournalEvent, Outcome
from stackowl.journal import record as journal_record
from stackowl.journal.objective_events import ObjectiveSetAttrs, _objective_record_ref
from stackowl.objectives.model import Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID

SET_OBJECTIVE = "scheduling.set_objective"

#: Story 4.7's own duplicated mapping (``scheduler.py``/``scheduler_mutations.py``'s
#: identical dict) -- this module cannot import EITHER of those (no reason
#: to couple ``objectives/`` to ``scheduler/`` for one lookup table).
_REQUESTER_KIND_TO_ACTOR_KIND: dict[str, ActorKind] = {
    "owner": ActorKind.OWNER,
    "owl": ActorKind.OWL,
    "autonomous": ActorKind.AUTONOMOUS,
    "voice-unverified": ActorKind.VOICE_WORKER,
}


def _actor_kind_for_requester(requester_kind: str) -> ActorKind:
    return _REQUESTER_KIND_TO_ACTOR_KIND.get(requester_kind, ActorKind.AUTONOMOUS)


class SetObjectivePayload(BaseModel):
    """Everything ``objective_tool.execute()`` has ALREADY computed by the
    time it submits: the minted ``objective_id``, the durable delivery
    target, an optional EPIC's repo/branch triple, and the (already
    decomposed, upstream of this command — AD-26) ordered sub-goals."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    channel: str | None = None
    session_key: str | None = None
    target_channels: list[str] = Field(default_factory=list)
    target_addresses: dict[str, str | int] = Field(default_factory=dict)
    repo: str | None = None
    integration_branch: str | None = None
    base_branch: str | None = None
    subgoals: list[SubgoalSpec] = Field(default_factory=list)


async def _set_objective_handler(
    payload: SetObjectivePayload, context: CommandContext,
) -> CommandOutcome:
    from stackowl.pipeline.services import get_services

    db = get_services().db_pool
    if db is None:
        log.engine.warning(
            "[commands] objectives.commands._set_objective_handler: no db pool",
            extra={"_fields": {"objective_id": payload.objective_id}},
        )
        return CommandOutcome(
            success=False, error="objectives unavailable (no database configured)",
        )

    # Review fix (Story 4.7) -- a READ-ONLY receipt check FIRST, before
    # git-branch OR any persistence. The receipt WRITE moves all the way to
    # the END (combined with the final objective.set journal_record, below),
    # only after every ObjectiveStore write has already succeeded. This is
    # what makes "already executed" mean "actually fully done": the OLD
    # ordering wrote the receipt before persistence, so a crash between the
    # two left the command permanently marked done with nothing ever
    # created, AND a retry of an already-fully-succeeded repo-bearing
    # objective re-ran `git branch` forever ("already exists", no recovery).
    # A retry that finds no receipt here starts over from a clean slate
    # (git branch included, since nothing committed yet); a retry that finds
    # one here means everything already committed, and short-circuits
    # without touching git or the store at all.
    existing_receipt = await db.fetch_all(
        "SELECT 1 FROM command_receipts WHERE command_id = ?", (context.command_id,),
    )
    if existing_receipt:
        log.engine.info(
            "[commands] objectives.commands._set_objective_handler: command "
            "already executed — no-op (lease-reclaim re-run)",
            extra={"_fields": {
                "objective_id": payload.objective_id, "command_id": context.command_id,
            }},
        )
        return CommandOutcome(
            success=True, result={"objective_id": payload.objective_id, "created": False},
        )

    # Design Notes: git-branch still runs BEFORE persistence -- fails loud,
    # never leaves an objective row for a repo whose branch creation failed.
    if payload.repo:
        assert payload.integration_branch is not None  # objective_tool sets both together
        from stackowl.tools.system.shell import run_argv

        branch_result = await run_argv(
            ["git", "branch", payload.integration_branch],
            tool_name="git", workdir=payload.repo, intent="write",
        )
        if not branch_result.success:
            log.engine.warning(
                "[commands] objectives.commands._set_objective_handler: "
                "could not create integration branch",
                extra={"_fields": {
                    "objective_id": payload.objective_id, "repo": payload.repo,
                }},
            )
            return CommandOutcome(
                success=False,
                error=f"could not create integration branch: {branch_result.error}",
            )

    # A GENUINE concurrent race (two workers both pass the read-only check
    # above for the SAME command_id before either persists) fails LOUD here:
    # `objectives.PRIMARY KEY (owner_id, objective_id)` (migration 0066)
    # refuses the second INSERT for the same objective_id, raising out of
    # this handler rather than silently duplicating or lying about success.
    objective = Objective(
        objective_id=payload.objective_id,
        owner_id=DEFAULT_PRINCIPAL_ID,
        intent=payload.intent,
        channel=payload.channel,
        session_key=payload.session_key,
        target_channels=list(payload.target_channels),
        target_addresses=dict(payload.target_addresses),
        repo=payload.repo,
        integration_branch=payload.integration_branch,
        base_branch=payload.base_branch,
    )
    store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
    await store.create(objective)
    await store.append_event(payload.objective_id, "created", payload.intent)
    await store.add_subgoals(payload.objective_id, list(payload.subgoals))
    await store.append_event(
        payload.objective_id, "decomposed", f"{len(payload.subgoals)} step(s)",
    )

    # THE RECEIPT WRITE, LAST -- combined with the objective.set journal
    # event in ONE transaction, only now that persistence has already fully
    # succeeded above.
    async with db.transaction() as conn:
        newly = await record_command_execution(
            conn, context.command_id, context.command_type,
        )
        if not newly:
            # The remaining sliver the PRIMARY KEY guard above does not
            # cover: another worker's receipt WRITE landed in the window
            # between our own persistence completing and this transaction
            # opening. Exactly one receipt exists either way, and the
            # winner's own journal_record already ran -- do not double-write it.
            log.engine.warning(
                "[commands] objectives.commands._set_objective_handler: lost a "
                "race to a concurrent caller for the same command_id after "
                "persisting — not double-journaling",
                extra={"_fields": {
                    "objective_id": payload.objective_id, "command_id": context.command_id,
                }},
            )
            return CommandOutcome(
                success=True, result={"objective_id": payload.objective_id, "created": True},
            )
        await journal_record(conn, JournalEvent(
            type="objective.set",
            schema_version=1,
            actor_kind=_actor_kind_for_requester(context.requester_kind),
            actor_id=context.requester_kind,
            target_kind=ActorKind.OWNER,
            target_id=payload.objective_id,
            outcome=Outcome.OK,
            record_ref=_objective_record_ref(payload.objective_id),
            attrs=ObjectiveSetAttrs(command_id=context.command_id),
        ))

    return CommandOutcome(
        success=True,
        result={
            "objective_id": payload.objective_id,
            "created": True,
            "subgoals": [s.description for s in payload.subgoals],
            "step_count": len(payload.subgoals),
        },
    )


def _register() -> None:
    set_objective_spec = CommandSpec(
        command_type=SET_OBJECTIVE,
        payload_model=SetObjectivePayload,
        severity="write",
        # Irreversible: an objective's creation cannot be undone (Design
        # Notes: this forces step-up for EVERY requester kind, including the
        # owner — the gate's honest, unmodified behavior for a genuinely
        # one-way action).
        reversible=False,
    )
    CommandSpecRegistry.register(set_objective_spec)
    CommandHandlerRegistry.register(SET_OBJECTIVE, _set_objective_handler)  # type: ignore[arg-type]
    log.engine.info(
        "[commands] objectives.commands: CommandSpec registered",
        extra={"_fields": {"command_types": [SET_OBJECTIVE]}},
    )


_register()
