"""Support classes for ``BatchApproveTool`` — models, rendering, audited execution.

Extracted from ``batch_approve.py`` so each file stays within the source-size
boundary (B2) while keeping the design object-oriented:

* :class:`BatchAction` / :class:`BatchApproveArgs` — frozen, ``extra=forbid``
  argument models.
* :class:`BatchRenderer` — pure presentation of the numbered plan and the
  approved/rejected result frames (no I/O).
* :class:`BatchAuditor` — the bounded, audited window: appends the batch grant /
  rejection and each action outcome to the injected :class:`AuditLogger`
  (self-healing — auditing never crashes the batch).
* :class:`BatchExecutor` — runs each approved action DIRECTLY (pre-consented, so
  the per-action consent gate is bypassed on purpose), surfacing every outcome,
  catching a failing action (B5) so the others still run and nothing raises.

The tool (``batch_approve.py``) owns only the consent round-trip and wiring; the
mechanics live here.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from stackowl.infra.observability import log
from stackowl.interaction.clarify_gateway import OUTCOME_ANSWERED
from stackowl.pipeline.services import StepServices
from stackowl.tools.base import ToolResult
from stackowl.tools.child_exclusion import child_excluded_now
from stackowl.tools.registry import ToolRegistry

# Hard cap on the number of actions one batch may present (an unreadable plan the
# user cannot meaningfully consent to is rejected instead).
MAX_ACTIONS = 10

# The two batch choices. "Approve all" runs the whole plan; "Reject" runs none.
# (FF-J8-1: a per-action "pick subset" affordance is deferred — see the addendum.)
APPROVE = "Approve all"
REJECT = "Reject"

# Audit event types for the bounded, audited window.
AUDIT_GRANT = "batch_approval.granted"
AUDIT_REJECT = "batch_approval.rejected"
AUDIT_ACTION = "batch_approval.action"


class BatchAction(BaseModel):
    """One planned consequential action inside a batch.

    ``tool`` must name a REAL registered tool; ``args`` are passed verbatim to
    that tool's ``execute`` once the batch is approved; ``summary`` is the
    one-line human-readable description shown in the numbered plan.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1)
    args: dict[str, object] = Field(default_factory=dict)
    summary: str = Field(min_length=1)


class BatchApproveArgs(BaseModel):
    """Validated arguments for one ``batch_approve`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intro: str = Field(min_length=1)
    actions: list[BatchAction] = Field(min_length=1, max_length=MAX_ACTIONS)


class BatchRenderer:
    """Pure presentation of the plan + result frames (no I/O, no audit)."""

    @staticmethod
    def _target(a: BatchAction) -> str:
        """The TRUSTED action target shown on the consent surface — the real tool
        name + a bounded view of its args. CONSENT INTEGRITY: the free-text
        ``summary`` is model-authored and could misdescribe the action, so the
        actual ``tool`` (and args) that WILL execute are always rendered here — the
        user can never approve "send a reminder" while a different tool runs."""
        if a.args:
            preview = ", ".join(
                f"{k}={(str(v)[:40] + '…') if len(str(v)) > 40 else str(v)}"
                for k, v in list(a.args.items())[:6]
            )
            return f"runs `{a.tool}`({preview})"
        return f"runs `{a.tool}`()"

    @staticmethod
    def plan(args: BatchApproveArgs) -> str:
        """Render the numbered plan shown above the Approve all / Reject buttons.

        Each line pairs the model's ``summary`` with the TRUSTED ``tool``+args that
        will actually run (see :meth:`_target`) so the consent surface cannot lie.
        """
        lines = [args.intro, "", "I will:"]
        for i, a in enumerate(args.actions, start=1):
            lines.append(f"{i}. {a.summary} — {BatchRenderer._target(a)}")
        lines.append("")
        lines.append("Approve all of these, or reject?")
        return "\n".join(lines)

    @staticmethod
    def rejected(args: BatchApproveArgs, outcome: str) -> str:
        reason = (
            "you rejected the plan"
            if outcome == OUTCOME_ANSWERED
            else "no approval was received in time"
        )
        return (
            f"The user did NOT approve the {len(args.actions)}-action plan "
            f"({reason}). NONE of the actions were executed. Acknowledge that "
            "nothing was run and ask whether the user wants to change the plan."
        )

    @staticmethod
    def executed(
        outcomes: list[dict[str, object]], n_succeeded: int, n_failed: int,
    ) -> str:
        lines = [
            "The user approved the plan in ONE batch. Executed "
            f"{len(outcomes)} action(s): {n_succeeded} succeeded, {n_failed} failed.",
        ]
        for o in outcomes:
            status = "OK" if o["success"] else f"FAILED ({o['error']})"
            lines.append(f"{o['n']}. {o['summary']} [{o['tool']}] — {status}")
        return "\n".join(lines)


class BatchAuditor:
    """Append the audited batch window to the injected AuditLogger (never raises)."""

    def __init__(
        self, services: StepServices, actor: str, clock: Callable[[], float],
    ) -> None:
        self._logger = services.audit_logger
        self._actor = actor
        self._clock = clock

    def _append(self, event_type: str, session_id: str, details: dict[str, object]) -> None:
        if self._logger is None:
            log.tool.debug(
                "batch_approve.execute: no audit_logger wired — skipping audit",
                extra={"_fields": {"event_type": event_type}},
            )
            return
        try:
            self._logger.append(
                event_type=event_type,
                actor=self._actor,
                target=session_id,
                details={**details, "ts": self._clock()},
            )
        except Exception as exc:  # B5 — auditing must never crash the batch
            log.tool.error(
                "batch_approve.execute: audit append failed — continuing",
                exc_info=exc,
                extra={"_fields": {"event_type": event_type, "session_id": session_id}},
            )

    def grant(self, session_id: str, args: BatchApproveArgs) -> None:
        """Open the bounded, audited window."""
        self._append(
            AUDIT_GRANT,
            session_id,
            {"intro": args.intro, "n_actions": len(args.actions),
             "tools": [a.tool for a in args.actions]},
        )

    def reject(self, session_id: str, args: BatchApproveArgs, outcome: str, answer: str | None) -> None:
        self._append(
            AUDIT_REJECT,
            session_id,
            {"intro": args.intro, "n_actions": len(args.actions),
             "outcome": outcome, "answer": answer},
        )

    def action(self, session_id: str, action: BatchAction, *, success: bool, error: str | None) -> None:
        self._append(
            AUDIT_ACTION,
            session_id,
            {"tool": action.tool, "summary": action.summary,
             "success": success, "error": error or ""},
        )


class BatchExecutor:
    """Run each approved action directly (pre-consented), auditing each outcome."""

    def __init__(self, registry: ToolRegistry, auditor: BatchAuditor) -> None:
        self._registry = registry
        self._auditor = auditor

    async def run(
        self, args: BatchApproveArgs, session_id: str,
    ) -> tuple[list[dict[str, object]], int, int]:
        """Execute every action; return ``(outcomes, n_succeeded, n_failed)``.

        The batch GRANT is audited first (the window opens), then each action runs
        DIRECTLY — pre-consented (the batch approval IS the consent), so the
        per-action dispatch consent gate is bypassed on purpose. A failing action
        is caught, logged (B5), surfaced in ``outcomes``, and audited — the other
        actions still run and nothing raises.
        """
        self._auditor.grant(session_id, args)
        outcomes: list[dict[str, object]] = []
        n_succeeded = 0
        for i, action in enumerate(args.actions, start=1):
            outcome = await self._run_one(i, action, session_id)
            if outcome["success"]:
                n_succeeded += 1
            outcomes.append(outcome)
        n_failed = len(outcomes) - n_succeeded
        log.tool.info(
            "batch_approve.execute: actions executed",
            extra={"_fields": {"n_actions": len(args.actions),
                               "succeeded": n_succeeded, "failed": n_failed}},
        )
        return outcomes, n_succeeded, n_failed

    async def _run_one(
        self, n: int, action: BatchAction, session_id: str,
    ) -> dict[str, object]:
        # SEC-3 / F164 — DEFENSE-IN-DEPTH: an approved batch executes each action
        # DIRECTLY (pre-consented, bypassing the per-action dispatch consent gate).
        # That bypass must NOT also bypass the child-exclusion depth rail, so the
        # guard is re-applied per action here: a child-excluded tool (spawn /
        # delegate / process / execute_code / owl_build) at delegation_depth>0 is
        # REFUSED even inside an approved batch. (batch_approve already fails closed
        # in non-interactive/delegated contexts; this is belt-and-braces.)
        if child_excluded_now(action.tool):
            excl_err = (
                f"action {action.tool!r} is child-excluded at delegation_depth>0 — "
                "refused inside the batch (defense-in-depth)"
            )
            log.tool.warning(
                "batch_approve.execute: action refused — child-excluded at depth",
                extra={"_fields": {"n": n, "tool": action.tool}},
            )
            self._auditor.action(session_id, action, success=False, error=excl_err)
            return {"n": n, "tool": action.tool, "summary": action.summary,
                    "success": False, "error": excl_err}
        tool = self._registry.get(action.tool)
        if tool is None:  # defensive — validated earlier, but never trust drift
            self._auditor.action(session_id, action, success=False, error="tool no longer registered")
            return {"n": n, "tool": action.tool, "summary": action.summary,
                    "success": False, "error": "tool no longer registered"}
        try:
            result: ToolResult = await tool.execute(**action.args)
        except Exception as exc:  # B5 — surface, log, keep going, never raise
            log.tool.error(
                "batch_approve.execute: action raised — surfaced, continuing",
                exc_info=exc,
                extra={"_fields": {"n": n, "tool": action.tool}},
            )
            self._auditor.action(session_id, action, success=False, error=str(exc))
            return {"n": n, "tool": action.tool, "summary": action.summary,
                    "success": False, "error": str(exc)}
        if not result.success:
            log.tool.warning(
                "batch_approve.execute: action returned failure — surfaced, continuing",
                extra={"_fields": {"n": n, "tool": action.tool, "error": result.error}},
            )
        err = None if result.success else (result.error or "")
        self._auditor.action(session_id, action, success=result.success, error=err)
        return {"n": n, "tool": action.tool, "summary": action.summary,
                "success": result.success,
                "output": result.output if result.success else "",
                "error": err}
