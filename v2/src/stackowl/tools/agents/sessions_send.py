"""SessionsSendTool — continue an existing named owl session (E8-S4).

KEY DESIGN — this is a CONTINUE-RUN, not a mailbox-post. A spawned session (E8-S3)
is a registry entry ``label → (owl_name, …)``, not a running actor — there is no
per-session reader loop to post to. So ``sessions_send`` looks the session up by
label and runs its owl pipeline ONCE with ``session_id=f"session:{label}"`` + the
new message (under the shared ``delegation_governor``, ``delegation_depth=1`` so it
is depth-gated and cannot itself spawn/delegate), then returns the reply. Continuity
flows THROUGH the MemoryBridge: ``classify`` reads prior turns by ``session:{label}``
(seeding ``state.history``) and ``consolidate`` writes this turn back under the same
id — the tool seeds NO history and stores NONE on the handle. Synchronous, so
"wait-for-reply" is inherent and timeout-bounded; ``wait=False`` still runs the turn
(no async actor) but returns without the reply text (status ``"sent"``).

THREE rails, all self-healing / no-hidden-errors:
* **Unknown session** — a ``registry.get`` miss → structured refusal, NOT an
  auto-spawn (a typo must surface), never raises.
* **Rate-limit** — a per-session :class:`TokenBucket` keyed by the SERVER-derived
  session id (never caller-controlled) caps bursts so a runaway loop cannot flood.
* **Run failure / governor-saturation / timeout** — logged ERROR and returned as a
  structured ``status='error'`` / ``'timeout'`` record; the session is PRESERVED
  (history untouched), never raises, never a stuck handle.

Origin is SERVER-STAMPED from :class:`stackowl.infra.trace.TraceContext` (the true
caller owl) — there is no ``from``/origin arg, so a caller cannot spoof who sent.
Severity ``write``; ``toolset_group`` ``agents``. In ``_CHILD_EXCLUDED_TOOLS`` so a
delegated child (delegation_depth>0) cannot drive sessions.
"""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.owls.session_registry import SessionRegistry
from stackowl.pipeline.authz_compose import resolve_owl_bounds
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import get_services
from stackowl.pipeline.state import PipelineState
from stackowl.tools.agents._send_results import error as _error
from stackowl.tools.agents._send_results import failed as _failed
from stackowl.tools.agents._send_results import ok as _ok
from stackowl.tools.agents._send_results import refused as _refused
from stackowl.tools.agents._send_results import run_under_governor as _run_under_governor
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.webhooks.rate_limit import TokenBucket

_TOOLSET_GROUP = "agents"
_DEFAULT_CALLER = "secretary"

# Per-session send budget — a hard ceiling so a runaway loop cannot flood a session.
_SEND_MAX_PER_WINDOW = 10
_SEND_WINDOW_SECONDS = 60
# Wall-clock ceiling on one continue-run (the governor acquire is separately bounded);
# past this the send returns a structured timeout and the session is kept untouched.
_SEND_TIMEOUT_SECONDS = 60.0

SESSIONS_SEND_DESCRIPTION = (
    "Continue an existing named owl session by sending it a follow-up message. "
    "Provide the session's 'label' (its address) and the 'message' to send; the "
    "session's owl answers with its persisted conversation history in context, and "
    "the reply is returned. Set 'wait' to false to send without waiting for the "
    "reply text. Refuses (without crashing) if the label is unknown or the session "
    "is being messaged too fast."
)

SESSIONS_SEND_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "Address of the session to continue."},
        "message": {"type": "string", "description": "The follow-up message to send."},
        "wait": {"type": "boolean", "description": "Wait for and return the reply text (default true)."},
    },
    "required": ["label", "message"],
    "additionalProperties": False,
}


class SessionsSendArgs(BaseModel):
    """Validated arguments for one ``sessions_send`` invocation.

    Note there is deliberately NO ``from``/origin field — the caller owl is
    server-stamped from TraceContext so it cannot be spoofed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    message: str
    wait: bool = True


class SessionsSendTool(Tool):
    """Continue a named persistent owl session (a synchronous continue-run)."""

    def __init__(self) -> None:
        # Per-session rate limiter — keyed by the SERVER-derived session id (never
        # a caller-controlled value) so a runaway loop cannot flood a session.
        self._rate = TokenBucket(
            max_tokens=_SEND_MAX_PER_WINDOW, window_seconds=_SEND_WINDOW_SECONDS,
        )

    @property
    def name(self) -> str:
        return "sessions_send"

    @property
    def description(self) -> str:
        return SESSIONS_SEND_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return SESSIONS_SEND_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute
    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "sessions_send.execute: entry",
            extra={"_fields": {"has_label": "label" in kwargs, "has_message": "message" in kwargs}},
        )
        try:
            args = SessionsSendArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "sessions_send.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return _failed(f"sessions_send: invalid arguments — {exc.errors()!r}", t0)

        ctx = TraceContext.get()
        trace_id = str(ctx.get("trace_id") or ctx.get("session_id") or "sessions-send")
        # Origin is SERVER-STAMPED (the true caller owl) — never a tool arg.
        caller = str(ctx.get("owl_name") or _DEFAULT_CALLER)
        channel = str(ctx.get("channel") or "internal")

        services = get_services()
        registry = services.session_registry
        if registry is None:
            log.tool.warning(
                "sessions_send.execute: no session_registry wired — degraded",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return _refused(t0, "unavailable", "sessions are not available in this environment.")

        # 2. DECISION — unknown session is an explicit refusal, NOT an auto-spawn
        # (a typo must surface to the caller rather than silently create state).
        session = registry.get(args.label)
        if session is None:
            log.tool.warning(
                "sessions_send.execute: unknown session label — refusing",
                extra={"_fields": {"trace_id": trace_id, "label": args.label, "caller": caller}},
            )
            return _refused(
                t0, "unknown_session",
                f"no live session is labelled {args.label!r}; spawn it first or check the label.",
            )

        # Rate-limit keyed by the SERVER-side session identity (not caller-controlled).
        rate_key = f"session:{session.label}"
        if not self._rate.consume(rate_key):
            log.tool.warning(
                "sessions_send.execute: session rate-limited — refusing",
                extra={"_fields": {"trace_id": trace_id, "label": args.label, "owl": session.owl_name}},
            )
            return _refused(
                t0, "rate_limited",
                f"session {args.label!r} is being messaged too fast; slow down and retry shortly.",
            )

        # 3. STEP — the continue-run (self-healing: a failure keeps the session).
        return await self._continue_run(
            registry=registry,
            label=args.label,
            owl_name=session.owl_name,
            invoking_owl=caller,
            message=args.message,
            wait=args.wait,
            trace_id=trace_id,
            channel=channel,
            t0=t0,
        )

    # ---------------------------------------------------------------- helpers
    async def _continue_run(
        self,
        *,
        registry: SessionRegistry,
        label: str,
        owl_name: str,
        invoking_owl: str,
        message: str,
        wait: bool,
        trace_id: str,
        channel: str,
        t0: float,
    ) -> ToolResult:
        """Run the session's owl once under ``session:{label}`` + the new message.

        Builds a continue-run :class:`PipelineState` with empty seed history, depth=1
        (depth-gated), runs under the shared governor (bounded acquire, mirrors
        A2ADelegator). Continuity is the bridge's: ``classify`` fills ``state.history``
        from prior turns under ``session:{label}`` and ``consolidate`` persists this
        turn back. A failure / saturation / timeout is logged ERROR + surfaced
        structured; the session is PRESERVED.
        """
        # Continue-run state: history is EMPTY — classify reads the session's prior
        # turns from the bridge by session_id and OVERWRITES it. depth=1, non-
        # interactive (no user channel binding to answer a clarify — clarify
        # default-denies, never parks).
        services = get_services()
        sub_state = PipelineState(
            trace_id=trace_id or "sessions-send",
            session_id=f"session:{label}",
            input_text=message,
            channel=channel,
            owl_name=owl_name,
            pipeline_step="dispatch",
            interactive=False,
            delegation_depth=1,
            history=(),
            # E2-S2 delegation floor — the session child cannot exceed the INVOKING
            # owl's bounds even if its own persona is broader (FR35-runtime).
            # Best-effort: invoking owl unbounded → None (no clamp).
            creation_ceiling=resolve_owl_bounds(invoking_owl, services.owl_registry),
        )
        backend = AsyncioBackend(services=services)
        try:
            final_state = await asyncio.wait_for(
                _run_under_governor(backend, sub_state),
                timeout=_SEND_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            log.tool.error(
                "sessions_send._continue_run: timeout — session kept",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "label": label, "timeout_s": _SEND_TIMEOUT_SECONDS}},
            )
            return _error(t0, label, owl_name, "timeout", "the session did not reply in time; it is kept.")
        except Exception as exc:  # B5 — never raise out of the tool.
            log.tool.error(
                "sessions_send._continue_run: continue-run failed — session kept",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "label": label}},
            )
            return _error(t0, label, owl_name, "error", str(exc))

        reply = "".join(chunk.content for chunk in final_state.responses)
        # NO handle-history write — consolidate already persisted this turn to the
        # MemoryBridge under session:{label}; the NEXT send reads it via classify.
        # Just keep the session warm so the idle sweep spares it.
        registry.touch(label)

        if final_state.errors:
            # No-hidden-errors: a failed continue-run is REPORTED, not masked as a
            # fake reply. The user turn was persisted by consolidate, but status
            # tells the caller the owl's turn errored.
            log.tool.warning(
                "sessions_send._continue_run: continue-run reported errors — surfacing",
                extra={"_fields": {"trace_id": trace_id, "label": label,
                                   "errors": list(final_state.errors)}},
            )
            return _error(
                t0, label, owl_name, "error",
                f"the session's reply errored: {list(final_state.errors)!r}",
            )

        if not wait:
            # The run already happened (no async actor); we just omit the reply
            # text (status 'sent') for surface compatibility.
            log.tool.debug(
                "sessions_send._continue_run: wait=False — turn sent without reply text",
                extra={"_fields": {"trace_id": trace_id, "label": label, "reply_len": len(reply)}},
            )
            return _ok(
                {"label": label, "owl": owl_name, "status": "sent"}, t0,
                note=f"message sent to session {label!r} (reply not awaited)",
            )

        log.tool.debug(
            "sessions_send._continue_run: reply delivered (turn persisted to bridge)",
            extra={"_fields": {"trace_id": trace_id, "label": label, "reply_len": len(reply)}},
        )
        return _ok(
            {"label": label, "owl": owl_name, "reply": reply, "status": "delivered"}, t0,
            note=f"session {label!r} replied",
        )
