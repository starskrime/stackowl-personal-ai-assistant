"""HeartbeatRespondTool — declare a heartbeat turn's outcome + optional notify.

During a heartbeat (proactive/scheduled) turn the owl records what it concluded
(``outcome`` / ``summary``) and whether to ping the user (``notify``). On
``notify=True`` it builds a :class:`Notification` and hands it to the S0 transport
chokepoint ``get_services().proactive_deliverer.deliver(...)`` — never a channel
adapter directly, never re-deciding quiet-hours/focus (the router owns that).

Three properties make this a gate, not a megaphone:
* **Hard urgency gate (S0):** ``priority`` is run through :func:`clamp_agent_urgency`,
  so a ``critical`` request is neutralized to ``normal`` (``critical`` is reserved
  for user/job-config/system origin). The schema is permissive on purpose — the
  clamp is the single enforcement point (defense-in-depth), not a schema constraint.
* **Once-per-turn guard:** a second call in the same trace that actually delivered
  is refused; in-memory + BOUNDED ([[feedback_always_self_healing]]).
* **Self-healing:** missing deliverer / ``"failed"`` / any error → caught, logged
  (B5), surfaced as a structured ``deferred`` / ``failed`` record; never raises.

``next_check`` is RECORD-ONLY (the scheduler owns scheduling). Severity ``read``;
``toolset_group`` ``scheduling``. Provenance: BUILD-to-spec from a reference agent's
heartbeat-response primitive — see ``_bmad-output/research/tool-port-analysis.md``.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.notifications.deliverer import AgentUrgency, clamp_agent_urgency
from stackowl.notifications.router import Notification
from stackowl.notifications.router_helpers import resolve_target_chat_id
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "scheduling"
_CATEGORY = "heartbeat"

# Bound on the once-per-turn guard so it cannot grow without limit across a long
# server lifetime (FIFO eviction of the oldest trace ids).
_GUARD_MAX_ENTRIES = 2048


class HeartbeatRespondArgs(BaseModel):
    """Validated arguments for one ``heartbeat_respond`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: str
    notify: bool
    summary: str
    notification_text: str | None = None
    priority: str | None = None
    next_check: str | None = None


class HeartbeatRespondTool(Tool):
    """Declare a heartbeat turn's outcome and optionally notify the user."""

    def __init__(self, *, guard_max: int = _GUARD_MAX_ENTRIES) -> None:
        """Construct the singleton tool. The guard is a process-lifetime
        ``OrderedDict`` used as a bounded FIFO set keyed by ``trace_id``
        (oldest evicted once ``guard_max`` is exceeded)."""
        self._guard_max = guard_max
        self._responded: OrderedDict[str, None] = OrderedDict()

    @property
    def name(self) -> str:
        return "heartbeat_respond"

    @property
    def description(self) -> str:
        return (
            "Declare the OUTCOME of a heartbeat (proactive/scheduled) turn and "
            "whether to actually notify the user. Provide 'outcome' (a short tag "
            "of what you concluded), 'summary' (one or two lines for the log), and "
            "'notify' (true only if the user genuinely needs to hear about this "
            "now). When notify=true, optionally set 'notification_text' (the exact "
            "message to send; defaults to 'summary') and 'priority' (a hint — it is "
            "CLAMPED, you cannot send a critical alert). 'next_check' is an "
            "informational hint about when you'd next look; it does NOT reschedule "
            "anything. Call this AT MOST ONCE per turn. LANE: closing out a "
            "heartbeat turn with a decision to notify or stay quiet. ANTI-LANE: do "
            "NOT use this to schedule recurring work (use cronjob) or to ask the "
            "user something (use clarify)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "description": "Short tag of what the heartbeat turn concluded.",
                },
                "notify": {
                    "type": "boolean",
                    "description": "Send a notification now? true only if the user must hear it.",
                },
                "summary": {
                    "type": "string",
                    "description": "One or two lines summarizing the outcome (for the log).",
                },
                "notification_text": {
                    "type": "string",
                    "description": "Exact message to send when notify=true (defaults to summary).",
                },
                "priority": {
                    "type": "string",
                    "description": (
                        "Urgency hint when notify=true. CLAMPED for safety: only "
                        "'low' stays low, everything else becomes 'normal' — an "
                        "agent cannot raise a critical alert."
                    ),
                },
                "next_check": {
                    "type": "string",
                    "description": "Informational hint of when you'd next check. Does NOT reschedule.",
                },
            },
            "required": ["outcome", "notify", "summary"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "heartbeat_respond.execute: entry",
            extra={"_fields": {"notify": kwargs.get("notify"), "has_priority": "priority" in kwargs}},
        )

        try:
            args = HeartbeatRespondArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "heartbeat_respond.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"heartbeat_respond: invalid arguments — {exc.errors()!r}", t0)

        ctx = TraceContext.get()
        trace_id = str(ctx.get("trace_id") or "")
        channel = ctx.get("channel")
        session_id = str(ctx.get("session_id") or "")

        # 2. DECISION — once-per-turn guard: a 2nd call in this trace is refused.
        if trace_id and trace_id in self._responded:
            log.tool.info(
                "heartbeat_respond.execute: duplicate call in turn — blocked",
                extra={"_fields": {"trace_id": trace_id}},
            )
            record = self._record(args, delivery_status="blocked")
            return self._ok(
                record,
                t0,
                note="already responded this turn — no second delivery",
                extra={"blocked": True},
            )
        # 3. STEP — notify=False records only; notify=True delivers via S0 chokepoint.
        # The guard is recorded only when the turn actually placed a notification
        # (or the agent explicitly declined to notify) — a failed/deferred/empty
        # send delivered nothing, so the agent may retry within the turn (B5/self-heal).
        if not args.notify:
            if trace_id:
                self._remember(trace_id)
            log.tool.debug("heartbeat_respond.execute: notify=false — record only")
            record = self._record(args, delivery_status="skipped")
            return self._ok(record, t0, note="recorded; no notification requested")

        urgency = clamp_agent_urgency(args.priority or "normal")
        delivery_status = await self._deliver(args, urgency, channel, session_id)
        if trace_id and delivery_status in ("delivered", "batched", "suppressed"):
            self._remember(trace_id)
        record = self._record(args, delivery_status=delivery_status, urgency=urgency)

        # 4. EXIT
        return self._ok(
            record, t0,
            note=f"notification {delivery_status}",
            extra={"delivery_status": delivery_status},
        )

    # ---------------------------------------------------------------- helpers

    async def _deliver(
        self,
        args: HeartbeatRespondArgs,
        urgency: AgentUrgency,
        channel: object,
        session_id: str,
    ) -> str:
        """Build + hand the notification to the S0 deliverer; never raises.

        Returns the transport ``DeliveryStatus`` string, ``"skipped"`` when there is
        no message body, or ``"deferred"`` when no deliverer is wired / an unexpected
        error occurs (self-healing, B5). The originating ``session_id`` resolves to
        the recipient ``chat_id`` (where the channel makes that valid — telegram
        private chats) so the heartbeat ping reaches THAT chat, not the adapter's
        shared mutable ``_last_chat_id``.
        """
        message = (args.notification_text or args.summary).strip()
        if not message:
            log.tool.warning("heartbeat_respond._deliver: empty message — skipping send")
            return "skipped"

        deliverer = get_services().proactive_deliverer
        if deliverer is None:
            log.tool.warning(
                "heartbeat_respond._deliver: no proactive_deliverer wired — deferring",
            )
            return "deferred"

        channel_name = str(channel) if channel else None
        notification = Notification(
            message=message,
            urgency=urgency,
            category=_CATEGORY,
            channel_name=channel_name,
            target_chat_id=resolve_target_chat_id(channel_name, session_id),
        )
        try:
            status = await deliverer.deliver(notification)
        except Exception as exc:  # B5 — deliverer is contracted not to raise; belt-and-braces.
            log.tool.error(
                "heartbeat_respond._deliver: deliver raised — deferring",
                exc_info=exc,
                extra={"_fields": {"channel": channel_name, "urgency": urgency}},
            )
            return "deferred"

        if status == "failed":
            log.tool.warning(
                "heartbeat_respond._deliver: deliver returned failed",
                extra={"_fields": {"channel": channel_name}},
            )
        return status

    def _remember(self, trace_id: str) -> None:
        """Record ``trace_id`` in the bounded FIFO guard (evict oldest over cap)."""
        self._responded[trace_id] = None
        while len(self._responded) > self._guard_max:
            self._responded.popitem(last=False)

    @staticmethod
    def _record(
        args: HeartbeatRespondArgs,
        *,
        delivery_status: str,
        urgency: str | None = None,
    ) -> dict[str, object]:
        """Build the structured outcome record returned to the model."""
        return {
            "outcome": args.outcome,
            "notify": args.notify,
            "summary": args.summary,
            "notification_text": args.notification_text,
            "priority": urgency,  # the CLAMPED urgency actually used (None if not notifying)
            "next_check": args.next_check,
            "delivery_status": delivery_status,
        }

    def _ok(
        self,
        record: dict[str, object],
        t0: float,
        *,
        note: str,
        extra: dict[str, object] | None = None,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "heartbeat_respond.execute: exit",
            extra={
                "_fields": {
                    "success": True,
                    "delivery_status": record.get("delivery_status"),
                    "duration_ms": duration_ms,
                    **(extra or {}),
                }
            },
        )
        payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "heartbeat_respond.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
