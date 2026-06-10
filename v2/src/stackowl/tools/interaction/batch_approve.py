"""batch_approve — present N planned consequential actions as ONE batch consent.

The owl plans several consequential actions ("run my morning routine") and,
instead of triggering N separate per-action consent prompts, calls
``batch_approve`` with the whole plan. The tool IS the consent boundary: it
presents the numbered plan to the user as ONE inline keyboard
(``["Approve all", "Reject"]``), SUSPENDS the turn on the proven clarify
round-trip, and RESUMES when the user taps. On "Approve all" it executes each
listed action DIRECTLY — PRE-CONSENTED, because the batch presentation already
showed and obtained consent for every action — and AUDITS the batch grant plus
each action's outcome. On "Reject"/timeout it executes NOTHING and audits the
rejection.

Why severity ``write`` and NOT ``consequential``: the per-action
``ConsequentialActionGate`` fires once per dispatch. If ``batch_approve`` were
consequential the gate would prompt FIRST (one prompt) and then the tool would
prompt AGAIN (the batch) — a double-prompt. Severity ``write`` lets the dispatch
gate pass the tool through so the tool runs its OWN single batch consent. The
security property is preserved: the user still sees and approves EVERY listed
action — consent simply moves from per-action to per-batch (the J8 outcome).

Non-interactive contexts (cron / heartbeat / delegation — ``interactive`` is
False on the TraceContext) CANNOT ask a user: there is nobody to approve. The
tool fails CLOSED — it returns a structured "needs human approval" record and
executes NOTHING (mirrors ``clarify``'s non-interactive sentinel).

Self-healing throughout: a failing action is caught, ERROR-logged (B5), and
surfaced in the per-action outcomes — never masked, the others still run, and
``execute`` never raises out. The plan models, presentation, audited window, and
direct execution live in :mod:`_batch_support`.

Provenance: BUILD — this is the StackOwl brainstorming-Q8 batch-consent UX. It
reuses the live clarify round-trip and tool registry; no vendor pattern ported.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.clarify_gateway import CLARIFY_TTL_SECONDS, OUTCOME_ANSWERED
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.interaction._batch_support import (
    APPROVE,
    MAX_ACTIONS,
    REJECT,
    BatchApproveArgs,
    BatchAuditor,
    BatchExecutor,
    BatchRenderer,
)

# Re-export for tests + callers that key off the module-level cap.
_MAX_ACTIONS = MAX_ACTIONS

# Tools that open their OWN consent/suspend surface — refused inside a batch so
# the batch stays the single consent decision (no batch-in-a-batch recursion, no
# clarify prompt nested under an already-approved batch).
_BATCH_EXCLUDED_TOOLS = frozenset({"batch_approve", "clarify"})

# Result frame surfaced to the model when there is nobody to approve.
_NON_INTERACTIVE = (
    "batch_approval requires an interactive user to approve the plan, but this "
    "is a non-interactive context (cron/heartbeat/delegation). NO actions were "
    "executed. If a human needs to approve these actions, surface them for a "
    "later interactive turn; do NOT assume approval."
)


class BatchApproveTool(Tool):
    """Present N planned consequential actions as ONE batch consent (J8)."""

    def __init__(
        self,
        *,
        timeout_s: float = CLARIFY_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Store the batch-consent park timeout and the audit-timestamp clock.

        ``timeout_s`` bounds how long the batch prompt waits for the user's tap
        (defaults to the shared clarify TTL). ``clock`` is injected so audit
        timestamps are deterministic in tests.
        """
        self._timeout_s = timeout_s
        self._clock = clock

    @property
    def name(self) -> str:
        return "batch_approve"

    @property
    def description(self) -> str:
        return (
            "Present several planned CONSEQUENTIAL actions to the user as ONE "
            "batch and execute them only if the user approves the whole plan. "
            "Use this INSTEAD of triggering N separate consent prompts when you "
            "have planned multiple actions (e.g. 'run my morning routine'). "
            "Provide 'intro' (a one-line plan summary) and 'actions' (a list of "
            "{tool, args, summary}); each 'tool' must be a real tool name and "
            "'args' its arguments. The user sees the numbered plan with "
            "'Approve all' / 'Reject' and decides ONCE. On approval every action "
            "runs (pre-consented + audited); on reject NOTHING runs. "
            "LANE: batch-approve a multi-step plan of consequential actions. "
            "ANTI-LANE: do NOT use for a single action (call that tool directly) "
            "or for read-only steps."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "intro": {
                    "type": "string",
                    "description": "One-line summary of the plan (shown above the numbered actions).",
                },
                "actions": {
                    "type": "array",
                    "maxItems": MAX_ACTIONS,
                    "description": (
                        f"The planned actions (1-{MAX_ACTIONS}). Each is "
                        "{tool, args, summary}: 'tool' is a real tool name, "
                        "'args' its arguments, 'summary' a one-line description."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string", "description": "A real registered tool name."},
                            "args": {"type": "object", "description": "Arguments for that tool."},
                            "summary": {"type": "string", "description": "One-line human description."},
                        },
                        "required": ["tool", "summary"],
                    },
                },
            },
            "required": ["intro", "actions"],
        }

    @property
    def manifest(self) -> ToolManifest:
        # Severity ``write`` (NOT consequential): the batch presentation IS the
        # consent, so the per-action dispatch gate must pass this through — the
        # tool runs its OWN single batch consent (see the module docstring).
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="unconfirmed",
            toolset_group="interaction",
        )

    # --------------------------------------------------------------- execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        raw_actions = kwargs.get("actions")
        log.tool.info(
            "batch_approve.execute: entry",
            extra={"_fields": {"n_actions_raw": len(raw_actions) if isinstance(raw_actions, (list, tuple)) else 0}},
        )

        # Validate the args (frozen, extra=forbid). A malformed batch never raises.
        try:
            args = BatchApproveArgs.model_validate(
                {"intro": kwargs.get("intro"), "actions": raw_actions}
            )
        except ValidationError as exc:
            return self._err(f"batch_approve received an invalid plan: {exc.errors()[:3]}", t0)

        services = get_services()
        registry = services.tool_registry
        if registry is None:
            return self._err("batch_approve unavailable: no tool registry is configured.", t0)

        # Validate each action names a REAL registered tool BEFORE prompting, so
        # the user is never asked to approve a plan that cannot run.
        unknown = [a.tool for a in args.actions if registry.get(a.tool) is None]
        if unknown:
            return self._err(
                f"batch_approve plan names unknown tool(s): {sorted(set(unknown))}. "
                "Every action's 'tool' must be a real registered tool.",
                t0,
            )

        # The batch IS the single consent boundary — refuse to nest a tool that
        # opens its OWN consent/suspend surface inside it (batch_approve recursing,
        # or clarify), which would mean a second prompt inside an already-approved
        # batch and break the "one consent decision" contract.
        nested = [a.tool for a in args.actions if a.tool in _BATCH_EXCLUDED_TOOLS]
        if nested:
            return self._err(
                f"batch_approve cannot run {sorted(set(nested))} inside a batch — "
                "the batch is the single consent surface; these open their own.",
                t0,
            )

        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")

        # 2. DECISION — non-interactive contexts CANNOT ask anyone. Fail CLOSED:
        # execute nothing, return the structured needs-human record. Never assume.
        if not interactive:
            log.tool.info(
                "batch_approve.execute: non-interactive context — fail closed, no execution",
                extra={"_fields": {"channel": channel, "session_id": session_id}},
            )
            return self._ok(_NON_INTERACTIVE, t0, extra={"approved": False, "reason": "non_interactive"})

        if not session_id or not channel:
            return self._err(
                "batch_approve cannot ask the user: no channel context (missing "
                "session/channel). No actions were executed.",
                t0,
            )

        gateway = services.clarify_gateway
        if gateway is None:
            return self._err(
                "batch_approve unavailable: no clarify gateway is configured. "
                "No actions were executed.",
                t0,
            )

        # 3. STEP — present the numbered plan as ONE prompt with two choices and
        # SUSPEND on the proven clarify round-trip until the user taps.
        try:
            clarify_id = await gateway.ask(
                str(session_id), str(channel), BatchRenderer.plan(args),
                choices=(APPROVE, REJECT), blocking=True,
            )
            answer, outcome = await gateway.wait_for_answer(clarify_id, timeout=self._timeout_s)
        except Exception as exc:  # self-healing — never raise out of a tool
            log.tool.error(
                "batch_approve.execute: gateway ask/wait failed — degrading, no execution",
                exc_info=exc,
                extra={"_fields": {"channel": channel, "session_id": session_id}},
            )
            return self._err(
                "batch_approve could not present the plan (gateway error). No actions were executed.",
                t0,
            )

        auditor = BatchAuditor(services, self.name, self._clock)
        approved = outcome == OUTCOME_ANSWERED and answer == APPROVE

        # 4. EXIT — approve-all executes every action (pre-consented + audited);
        # reject/timeout executes nothing (audited).
        if not approved:
            auditor.reject(str(session_id), args, outcome, answer)
            log.tool.info(
                "batch_approve.execute: exit — plan NOT approved, nothing executed",
                extra={"_fields": {"outcome": outcome, "n_actions": len(args.actions)}},
            )
            return self._ok(
                BatchRenderer.rejected(args, outcome), t0,
                extra={"approved": False, "clarify_id": clarify_id, "executed": 0},
            )

        executor = BatchExecutor(registry, auditor)
        outcomes, n_ok, n_fail = await executor.run(args, str(session_id))
        log.tool.info(
            "batch_approve.execute: exit — plan approved + executed",
            extra={"_fields": {"executed": len(outcomes), "succeeded": n_ok, "failed": n_fail}},
        )
        return self._ok(
            BatchRenderer.executed(outcomes, n_ok, n_fail), t0,
            extra={"approved": True, "clarify_id": clarify_id,
                   "executed": len(outcomes), "succeeded": n_ok, "failed": n_fail},
        )

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _ok(output: str, t0: float, *, extra: dict[str, object] | None = None) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "batch_approve.execute: exit",
            extra={"_fields": {"success": True, "duration_ms": duration_ms, **(extra or {})}},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "batch_approve.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
