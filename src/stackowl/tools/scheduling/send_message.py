"""SendMessageTool — agent-initiated outbound text over the channel registry (E7-S3).

A thin delegate over the channel-adapter registry that lets the owl proactively
send text to a channel (the session channel by default, or a named cross-channel
target). Story 4.8 (AD-1) — it submits the declared, irreversible
``messaging.send_message`` command through ``commands/spec/submit.py::
submit_command`` rather than calling ``proactive_deliverer.deliver(...)``
directly; ``notifications/commands.py``'s handler is the ONLY place that still
touches the S0 transport chokepoint, clamping the urgency to ``normal``
(``clamp_agent_urgency``; an agent cannot raise a critical alert) and
respecting the router's quiet-hours/focus/cap decision. Being irreversible, an
ordinary send now parks for step-up before it runs — even for the owner
(``authz.action_policy.decide``'s honest behavior, no owner carve-out).

* **Consequential gate (automatic):** the manifest declares
  ``action_severity="consequential"``; the ConsequentialActionGate in the tool
  registry/pipeline fires the consent prompt BEFORE ``execute`` and fails CLOSED
  off-TTY (cron / non-interactive denial). The tool does NOT call the gate itself
  (mirrors ``knowledge/skill_manage.py``).
* **Per-session flood cap:** a process-lifetime :class:`TokenBucket` keyed by
  ``session_key`` (10 sends / 60s) — the runaway-loop guard. Over cap → "rate
  limited", no send.
* **Self-healing:** no target / blank text / unknown channel / missing deliverer
  / ``"failed"`` / a deliverer that raises → structured result, logged (B5),
  NEVER raises out of ``execute`` ([[feedback_always_self_healing]]).

``list`` enumerates the registered channel names so the model can pick a target.
Provenance: BUILD-to-documented-shape — see
``_bmad-output/research/tool-port-analysis.md`` (E7 ``send_message`` row).


Registration note (moved verbatim from ToolRegistry.with_defaults by D05.1,
when auto-discovery replaced the hand-written register() calls; the rationale
belongs with the tool, not with the line that used to construct it):

    send_message — agent-initiated outbound text; routes a clamped (normal)
    Notification through get_services().proactive_deliverer (the S0 transport
    chokepoint) at execute time. Consequential: the registry's consent gate
    fires before execute (fails closed off-TTY). No constructor wiring.
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.channels.registry import ChannelRegistry
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.notifications.router_helpers import resolve_recipient
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.webhooks.rate_limit import TokenBucket

_TOOLSET_GROUP = "scheduling"
_CATEGORY = "agent_message"

# Per-session flood cap: 10 sends / 60s. The runaway-loop guard the party wanted.
_FLOOD_MAX_SENDS = 10
_FLOOD_WINDOW_SEC = 60


class SendMessageArgs(BaseModel):
    """Validated arguments for one ``send_message`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = "send"
    text: str | None = None
    target: str | None = None


class SendMessageTool(Tool):
    """Send text to a channel (default: the session channel) or list channels."""

    def __init__(
        self,
        *,
        flood_max: int = _FLOOD_MAX_SENDS,
        flood_window_seconds: int = _FLOOD_WINDOW_SEC,
    ) -> None:
        """Construct the singleton tool. The per-session flood cap is a
        process-lifetime :class:`TokenBucket` keyed by ``session_key``."""
        self._bucket = TokenBucket(
            max_tokens=flood_max, window_seconds=flood_window_seconds
        )

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return (
            "Proactively send a plain-text message to the user over a channel. "
            "action='send' (default) delivers 'text' to 'target' (a channel name "
            "from action='list'); 'target' defaults to the channel this "
            "conversation is on, so set it ONLY for a cross-channel send. "
            "action='list' enumerates the channels you can send to. Sends are "
            "consent-gated, rate-limited, 'normal' urgency; under quiet hours / "
            "focus a send is DEFERRED ('batched'), arriving later — say so if urgent. "
            "LANE: pushing an unsolicited message (e.g. an update on a long task). "
            "ANTI-LANE: do NOT use to reply to the current turn (just answer), to "
            "close a heartbeat turn (use heartbeat_respond), or to ask the user "
            "something (use clarify)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send", "list"],
                    "description": "send (default) | list available channels",
                },
                "text": {
                    "type": "string",
                    "description": "Message body to send (required for action='send').",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Channel to send to. Defaults to the session channel; "
                        "set only for a cross-channel send."
                    ),
                },
            },
            "required": [],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            command_types=("messaging.send_message",),
            commit_coupling="unconfirmed",
            toolset_group=_TOOLSET_GROUP,
            effect_class="sends_message",
        )

    def post_condition(
        self, args: dict[str, object], result: ToolResult
    ) -> object | None:
        """ADR-1 — observe the transport ACK through the AcceptanceAuthority. The
        deliverer's ``delivery_status`` (distinct from the success bool) is the
        observation; only ``'delivered'`` is a confirmed delivery. ``None`` for a
        non-delivery call (``action='list'``). Keeps the self-stamp for flag-off
        byte-identical behavior (batched stays verified=False)."""
        from stackowl.tools.scheduling._delivery_postcondition import (
            delivery_post_condition,
        )

        return delivery_post_condition(result.output)

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "send_message.execute: entry",
            extra={"_fields": {"action": kwargs.get("action", "send")}},
        )

        try:
            args = SendMessageArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "send_message.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.errors()!r}", t0)

        action = args.action.strip().lower()
        # 2. DECISION — dispatch by validated action.
        if action == "list":
            return self._list(t0)
        if action != "send":
            return self._err(
                f"Unknown action {args.action!r}. Valid actions: send | list.", t0
            )
        return await self._send(args, t0)

    def _list(self, t0: float) -> ToolResult:
        """Enumerate registered channel names so the model can pick a target."""
        names = [a.channel_name for a in ChannelRegistry.instance().all()]
        log.tool.debug(
            "send_message.execute: list channels",
            extra={"_fields": {"count": len(names)}},
        )
        note = f"{len(names)} channel(s) available." if names else "no channels registered."
        record: dict[str, object] = {"action": "list", "channels": names, "note": note}
        return self._ok(record, t0, extra={"action": "list", "count": len(names)})

    async def _send(self, args: SendMessageArgs, t0: float) -> ToolResult:
        """Resolve target + flood-cap + deliver via the S0 chokepoint; never raises."""
        ctx = TraceContext.get()
        session_key = str(ctx.get("session_key") or "")
        ctx_channel = ctx.get("channel")
        trace_id = ctx.get("trace_id")

        # Target defaults to the session's originating channel (LLM-ergonomics).
        target = (args.target or "").strip() or (
            str(ctx_channel).strip() if ctx_channel else ""
        )
        if not target:
            return self._err(
                "no target channel: pass 'target' (see action='list') — this "
                "session has no originating channel to default to.",
                t0,
            )

        # Blank/empty body → never send an empty message.
        text = (args.text or "").strip()
        if not text:
            return self._err("blank text: provide a non-empty 'text' to send.", t0)

        # Per-session flood cap (runaway-loop guard). Key on session_key; when absent
        # (cron/non-interactive), fall back to a single PROCESS-WIDE constant — NOT
        # `target`, which the caller controls and could vary to mint a fresh bucket
        # per channel and evade the cap.
        flood_key = session_key or "_no_session_"
        if not self._bucket.consume(flood_key):
            log.tool.warning(
                "send_message.execute: flood cap hit — rejecting send",
                extra={"_fields": {"channel": target}},
            )
            return self._err(
                "rate limited: too many sends in a short window — try again shortly.",
                t0,
            )

        # Unknown channel → structured error, no deliver, no raise.
        if not self._channel_exists(target):
            log.tool.warning(
                "send_message.execute: unknown channel",
                extra={"_fields": {"channel": target}},
            )
            return self._err(
                f"unknown channel {target!r}: use action='list' to see options.", t0
            )

        status = await self._deliver(text, target, trace_id, session_key)
        record: dict[str, object] = {
            "action": "send", "target": target, "text": text,
            "urgency": "normal", "delivery_status": status,
        }
        # HONESTY (F-30): the success flag must reflect whether the byte reached the
        # user, not stay green while the failure hides in `delivery_status`.
        #   "delivered"  → success + verified  (observed reaching transport)
        #   "failed"     → success=False       (transport gave up after retry)
        #   "batched"/"suppressed"/"deferred" → success but verified=False (queued/
        #     not-yet-delivered; the floor/learner can tell this from a real send).
        if status == "failed":
            return self._err(
                f"delivery to {target!r} failed: the transport could not deliver "
                "the message after retry — it did NOT reach the user.",
                t0,
                record=record,
                extra={"action": "send", "channel": target, "delivery_status": status},
            )
        verified = status == "delivered"
        return self._ok(
            record, t0, note=f"send {status}", verified=verified,
            extra={"action": "send", "channel": target, "delivery_status": status},
        )

    async def _deliver(
        self, text: str, target: str, trace_id: object, session_key: str
    ) -> str:
        """Submit ``messaging.send_message`` through the one door; never raises.

        Story 4.8 (AD-1) — this no longer calls ``proactive_deliverer.deliver``
        directly; it submits the declared, irreversible ``messaging.
        send_message`` command instead. Returns the transport
        ``DeliveryStatus`` when the command actually ran, ``"pending_approval"``
        when the gate parked it awaiting step-up (an irreversible send needs
        one even for the owner — Design Notes: "no owner carve-out"), or
        ``"deferred"`` when no db/deliverer is wired or ``submit_command``
        itself raises (self-healing, B5). The originating ``session_key``
        resolves to the recipient ``chat_id`` (where the channel makes that
        valid — telegram private chats) BEFORE submission, since that
        resolution needs the caller's own ``TraceContext``/session store,
        neither of which a durable command payload carries.
        """
        from stackowl.commands.spec.submit import submit_command
        from stackowl.notifications.commands import SEND_MESSAGE, SendMessagePayload

        db = get_services().db_pool
        if db is None:
            log.tool.warning(
                "send_message._deliver: no db pool wired — deferring",
                extra={"_fields": {"channel": target}},
            )
            return "deferred"

        payload = SendMessagePayload(
            message=text,
            channel=target,
            category=_CATEGORY,
            # The log-row identity, NOT a once-ness guarantee — see
            # `Notification.notification_id`. A retry mints a new trace, so
            # this value differs between attempts by construction.
            notification_id=str(trace_id) if trace_id else None,
            target=await resolve_recipient(target, session_key, get_services().session_store),
        )
        try:
            submission = await submit_command(db, SEND_MESSAGE, payload)
        except Exception as exc:  # B5 — submission must never crash the caller.
            log.tool.error(
                "send_message._deliver: submit_command raised — deferring",
                exc_info=exc,
                extra={"_fields": {"channel": target}},
            )
            return "deferred"

        outcome = submission.outcome
        if outcome is None:
            log.tool.info(
                "send_message._deliver: pending approval — an irreversible "
                "send needs step-up",
                extra={"_fields": {"channel": target, "command_id": submission.command_id}},
            )
            return "pending_approval"
        if not outcome.success:
            log.tool.warning(
                "send_message._deliver: command failed",
                extra={"_fields": {"channel": target, "error": outcome.error}},
            )
            return "failed"
        status = str(outcome.result.get("delivery_status") or "delivered")
        return status

    @staticmethod
    def _channel_exists(name: str) -> bool:
        """True if ``name`` is a registered channel. Never raises (B5)."""
        try:
            ChannelRegistry.instance().get(name)
        except Exception:  # B5 — ChannelNotFoundError (or registry hiccup) → absent
            return False
        return True

    def _ok(
        self, record: dict[str, object], t0: float, *,
        note: str | None = None, verified: bool | None = None,
        extra: dict[str, object] | None = None,
    ) -> ToolResult:
        # 4. EXIT. ``verified`` is the reality check distinct from the self-reported
        # success: None (default) ⇒ nothing to verify (e.g. action='list') and the
        # result is byte-identical to pre-verification behavior; True ⇒ the byte was
        # observed reaching the transport; False ⇒ accepted-but-queued (not yet
        # delivered) so a downstream decider does NOT treat it as a real delivery.
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "send_message.execute: exit",
            extra={"_fields": {
                "success": True, "verified": verified,
                "duration_ms": duration_ms, **(extra or {}),
            }},
        )
        payload: dict[str, object] = {"record": record}
        if note is not None:
            payload["note"] = note
        out = json.dumps(payload, ensure_ascii=False)
        return ToolResult(
            success=True, output=out, verified=verified, duration_ms=duration_ms
        )

    @staticmethod
    def _err(
        msg: str, t0: float, *,
        record: dict[str, object] | None = None,
        extra: dict[str, object] | None = None,
    ) -> ToolResult:
        """Structured FAILED result (model knows nothing was sent); never raises.

        ``record`` carries the structured delivery record into ``output`` for the
        delivery-failure path (so ``delivery_status`` survives for backward-compat);
        pre-execution refusals pass none and keep an empty output.
        """
        msg = f"send_message: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "send_message.execute: exit",
            extra={"_fields": {
                "success": False, "error": msg,
                "duration_ms": duration_ms, **(extra or {}),
            }},
        )
        out = (
            json.dumps({"record": record}, ensure_ascii=False)
            if record is not None
            else ""
        )
        return ToolResult(
            success=False, output=out, error=msg, duration_ms=duration_ms
        )
