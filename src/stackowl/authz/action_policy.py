"""The action-policy gate -- one function decides HOW a command runs (Story
4.4, AD-27).

Story 4.3 built the door every command walks through; ``authz.requester.
principal_for`` is a deliberate stub that grants every severity to every
requester kind (its own docstring: "the real per-requester-kind/reversibility
POLICY decision is Story 4.4's action-policy gate"). This module is that
policy: :func:`decide` maps ``(severity, reversible, requester_kind)`` onto
one of three outcomes, and :func:`attends` answers AD-27's separate
"attendance" question (who is present to be asked at all).

PURE, DELIBERATELY. No I/O, no model call, no db/journal import -- every
input is already known by the time ``execute_command_task`` calls this,
right after the severity check (AD-1: "severity check via the principal ->
action-policy gate -> consent -> deterministic handler"). A gate that reads
anything external cannot be unit-tested as a plain decision table, and this
story's whole I/O matrix (the ``owner``/``owl``/``voice-unverified``/
``autonomous`` rows) is exactly that: a table, not a workflow.

SEVERITY OUTRANKS REVERSIBILITY, ALWAYS. FR: "Severity outranks
reversibility: CONSEQUENTIAL always needs a read-back plus step-up, even if
reversible." :func:`decide` checks ``severity``/``reversible`` FIRST, before
it ever looks at ``requester_kind`` -- an owner's CONSEQUENTIAL order needs
step-up exactly like an owl's does; the requester-kind branch below only ever
fires for a reversible, non-CONSEQUENTIAL command.

WHY ``attends()`` DOES NOT BRANCH ``decide()`` THIS STORY. FR32's full rule
("irreversible + no standing authority -> Needs-you") already falls out of
the plain severity/reversibility check above, since an irreversible command
ALWAYS needs step-up regardless of who is asking -- there is no standing-
authority table yet (Story 4.6) that could ever let attendance bypass that.
``attends()`` is declared and tested here so 4.5/4.6 have one place to
extend without touching this module's signature again (the same "declared,
structurally correct, not yet load-bearing" shape Story 4.3 used for
``CommandContext.nonce``/``utterance_id``) -- but :class:`ActionPolicyDecision.
attending` is carried for logging/telemetry only, never read back by
:func:`decide` itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from stackowl.authz.requester import RequesterKind
from stackowl.authz.severity import ALL_SEVERITIES, CONSEQUENTIAL
from stackowl.infra.observability import log

#: AD-27's closed outcome vocabulary. ``run_at_once`` -- the owner's own
#: reversible WRITE order, no read-back (FR34). ``needs_approval`` -- an
#: owl/crew request's deterministic read-back (FR35): reversible,
#: non-CONSEQUENTIAL, opened for approval but never itself requiring the
#: signed/Telegram step-up path. ``needs_step_up`` -- irreversible, any
#: CONSEQUENTIAL approval, or otherwise on the step-up list (FR36): only an
#: explicit Telegram-mediated answer can close it, never a spoken
#: ``voice-unverified`` "yes" (FR41).
ActionPolicyOutcome = Literal["run_at_once", "needs_approval", "needs_step_up"]


@dataclass(frozen=True)
class ActionPolicyDecision:
    """What the gate decided, and whether this requester kind attends at all
    (AD-27's attendance primitive, carried for telemetry -- see this module's
    docstring for why it does not itself branch ``outcome``)."""

    outcome: ActionPolicyOutcome
    attending: bool


def attends(requester_kind: RequesterKind) -> bool:
    """Is this requester kind PRESENT to be asked at all (AD-27)?

    Only ``autonomous`` (a scheduler/unattended run) is not attending --
    every interactive kind (``owner``, ``owl``, ``voice-unverified``) is, by
    definition of having originated a live request. Epic 4's own
    Requirements are explicit: "Scheduler and autonomous runs are never
    'attending'."
    """
    # 1. ENTRY
    log.engine.debug(
        "[authz] action_policy.attends: entry",
        extra={"_fields": {"requester_kind": requester_kind}},
    )
    # 2/3. DECISION+STEP -- one comparison; the whole closed vocabulary bar
    # "autonomous" attends.
    result = requester_kind != "autonomous"
    # 4. EXIT
    log.engine.debug(
        "[authz] action_policy.attends: exit",
        extra={"_fields": {"requester_kind": requester_kind, "attending": result}},
    )
    return result


def decide(
    *, severity: str, reversible: bool, requester_kind: RequesterKind,
) -> ActionPolicyDecision:
    """The one gate every command's execution passes through after its
    severity check (AD-1, AD-27).

    Raises :class:`ValueError` for an undeclared *severity* -- a caller bug
    (every real caller passes a ``CommandSpec.severity``, already validated
    against ``authz.severity.ALL_SEVERITIES`` at registration), never a
    protocol outcome this gate itself decides between.
    """
    # 1. ENTRY
    log.engine.debug(
        "[authz] action_policy.decide: entry",
        extra={"_fields": {
            "severity": severity, "reversible": reversible,
            "requester_kind": requester_kind,
        }},
    )
    if severity not in ALL_SEVERITIES:
        raise ValueError(
            f"action_policy.decide: severity={severity!r} is not one of "
            f"{sorted(ALL_SEVERITIES)}"
        )
    attending = attends(requester_kind)

    # 2. DECISION -- severity/reversibility FIRST, always (see module
    # docstring: "severity outranks reversibility, always"). CONSEQUENTIAL or
    # irreversible needs step-up for every requester kind, an owner's order
    # included -- no branch below this point ever downgrades it.
    outcome: ActionPolicyOutcome
    if severity == CONSEQUENTIAL or not reversible:
        outcome = "needs_step_up"
    # An owl/crew request always gets a deterministic read-back before it can
    # be approved (FR35) -- never run_at_once, even for a reversible,
    # non-CONSEQUENTIAL command.
    elif requester_kind == "owl":
        outcome = "needs_approval"
    # Owner, voice-unverified (reversible, non-CONSEQUENTIAL only -- FR41)
    # and autonomous all run at once here: attendance is declared, not
    # load-bearing, this story (see module docstring).
    else:
        outcome = "run_at_once"

    # 4. EXIT
    log.engine.debug(
        "[authz] action_policy.decide: exit",
        extra={"_fields": {
            "severity": severity, "reversible": reversible,
            "requester_kind": requester_kind, "outcome": outcome,
            "attending": attending,
        }},
    )
    return ActionPolicyDecision(outcome=outcome, attending=attending)
