"""SendFileTool — agent-initiated outbound file/media over the channel registry (E8).

The genuine missing channel primitive: the agent can DOWNLOAD a file via shell
commands but, until now, could only send TEXT back to the user — never the bytes.
Sending a binary file needs the bot API plus the live chat context, so this is a
core tool (like ``send_message``), NOT something the agent assembles via shell.

A thin delegate over the S0 transport chokepoint that threads a *workspace-scoped*
file path through ``get_services().proactive_deliverer.deliver(...)`` — NEVER a
channel adapter directly — so every send respects the router's
quiet-hours/focus/cap decision and the urgency is HARD-CLAMPED to ``normal``
(``clamp_agent_urgency``; an agent cannot raise a critical alert). The deliverer
routes a notification carrying ``file_path`` to the channel adapter's
``send_file`` (Telegram → send_video/send_photo/send_document by extension).

Mirrors ``send_message`` exactly:

* **Consequential gate (automatic):** the manifest declares
  ``action_severity="consequential"`` — sending data out is consent-gated. The
  ConsequentialActionGate fires the consent prompt BEFORE ``execute`` and fails
  CLOSED off-TTY (cron / non-interactive denial). The tool does NOT call the gate
  itself.
* **Workspace scoping:** the file MUST exist, be a regular file, and resolve to a
  path UNDER the StackOwl workspace dir (``StackowlHome.workspace()``) — an
  arbitrary absolute path or a ``..`` traversal outside the workspace is rejected
  with a structured error and no send (no exfiltration of host files).
* **Size cap:** files larger than ``max_bytes`` (default 50 MB — the Telegram bot
  API document ceiling) are rejected structured; a bot cannot upload past it.
* **Per-session flood cap:** a process-lifetime :class:`TokenBucket` keyed by
  ``session_id`` — a file send counts against it (runaway-loop guard).
* **Self-healing:** missing file / outside workspace / too large / no target /
  unknown channel / missing deliverer / ``"failed"`` / a deliverer that raises →
  structured result, logged (B5), NEVER raises out of ``execute``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.channels.registry import ChannelRegistry
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.notifications.deliverer import clamp_agent_urgency
from stackowl.notifications.router import Notification
from stackowl.notifications.router_helpers import resolve_target_chat_id
from stackowl.paths import StackowlHome
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.webhooks.rate_limit import TokenBucket

_TOOLSET_GROUP = "scheduling"
_CATEGORY = "agent_file"

# Per-session flood cap: 10 sends / 60s (same as send_message).
_FLOOD_MAX_SENDS = 10
_FLOOD_WINDOW_SEC = 60

# Telegram bot API caps a document upload at ~50 MB; reject larger files up front
# with a structured error rather than failing mid-upload.
_MAX_BYTES = 50 * 1024 * 1024


class SendFileArgs(BaseModel):
    """Validated arguments for one ``send_file`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file_path: str
    caption: str | None = None
    target: str | None = None


class SendFileTool(Tool):
    """Send a workspace file/media to the user over a channel (default: session)."""

    def __init__(
        self,
        *,
        flood_max: int = _FLOOD_MAX_SENDS,
        flood_window_seconds: int = _FLOOD_WINDOW_SEC,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        """Construct the singleton tool. The per-session flood cap is a
        process-lifetime :class:`TokenBucket` keyed by ``session_id``."""
        self._bucket = TokenBucket(
            max_tokens=flood_max, window_seconds=flood_window_seconds
        )
        self._max_bytes = max_bytes

    @property
    def name(self) -> str:
        return "send_file"

    @property
    def description(self) -> str:
        return (
            "Send a file or media (a video, image, document, etc.) to the user "
            "over a channel. 'file_path' MUST be a file that already exists in the "
            "workspace (e.g. something you downloaded) — produce/download it FIRST, "
            "then send it; a bare/relative name resolves under the workspace. "
            "Arbitrary host paths are rejected. "
            "'caption' is an optional message attached to the file. "
            "'target' defaults to the channel this conversation is on; set it only "
            "for a cross-channel send. Sends are consent-gated, rate-limited and "
            "size-capped (~50 MB; a larger file cannot be sent). "
            "LANE: delivering bytes the user asked for (a downloaded clip, a "
            "generated report). ANTI-LANE: do NOT use to send plain text (use "
            "send_message) or to send a file outside the workspace."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Path of a file that already exists in the StackOwl "
                        "workspace. Produce/download the file FIRST, then send it; "
                        "a relative name is resolved under the workspace."
                    ),
                },
                "caption": {
                    "type": "string",
                    "description": "Optional caption/message to attach to the file.",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Channel to send to. Defaults to the session channel; "
                        "set only for a cross-channel send."
                    ),
                },
            },
            "required": ["file_path"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="consequential",
            commit_coupling="unconfirmed",
            toolset_group=_TOOLSET_GROUP,
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "send_file.execute: entry",
            extra={"_fields": {"has_caption": "caption" in kwargs}},
        )

        try:
            args = SendFileArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "send_file.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"invalid arguments — {exc.errors()!r}", t0)

        return await self._send(args, t0)

    async def _send(self, args: SendFileArgs, t0: float) -> ToolResult:
        """Validate the file + resolve target + flood-cap + deliver; never raises."""
        # 2. DECISION — validate the file is a workspace-scoped, in-limits regular file.
        resolved, file_err = self._resolve_workspace_file(args.file_path)
        if file_err is not None:
            log.tool.warning(
                "send_file.execute: file rejected",
                extra={"_fields": {"reason": file_err}},
            )
            return self._err(file_err, t0)
        assert resolved is not None  # narrowed by file_err is None

        ctx = TraceContext.get()
        session_id = str(ctx.get("session_id") or "")
        ctx_channel = ctx.get("channel")
        trace_id = ctx.get("trace_id")

        # Target defaults to the session's originating channel (LLM-ergonomics).
        target = (args.target or "").strip() or (
            str(ctx_channel).strip() if ctx_channel else ""
        )
        if not target:
            return self._err(
                "no target channel: pass 'target' (a channel name) — this session "
                "has no originating channel to default to.",
                t0,
            )

        # Per-session flood cap (runaway-loop guard). Key on session_id; when absent
        # (cron/non-interactive), fall back to a single PROCESS-WIDE constant — NOT
        # `target`, which the caller controls and could vary to evade the cap.
        flood_key = session_id or "_no_session_"
        if not self._bucket.consume(flood_key):
            log.tool.warning(
                "send_file.execute: flood cap hit — rejecting send",
                extra={"_fields": {"channel": target}},
            )
            return self._err(
                "rate limited: too many sends in a short window — try again shortly.",
                t0,
            )

        # Unknown channel → structured error, no deliver, no raise.
        if not self._channel_exists(target):
            log.tool.warning(
                "send_file.execute: unknown channel",
                extra={"_fields": {"channel": target}},
            )
            return self._err(
                f"unknown channel {target!r}: that channel is not registered.", t0
            )

        caption = (args.caption or "").strip()
        status = await self._deliver(str(resolved), caption, target, trace_id, session_id)
        record: dict[str, object] = {
            "action": "send_file",
            "target": target,
            "file_path": str(resolved),
            "caption": caption,
            "urgency": "normal",
            "delivery_status": status,
        }
        # HONESTY (F-29): the success flag must reflect whether the bytes reached the
        # user, not stay green while the failure hides in `delivery_status`.
        #   "delivered"  → success + verified  (observed reaching transport)
        #   "failed"     → success=False       (transport gave up after retry)
        #   "batched"/"suppressed"/"deferred" → success but verified=False (queued/
        #     not-yet-delivered; the floor/learner can tell this from a real send).
        if status == "failed":
            return self._err(
                f"delivery to {target!r} failed: the transport could not upload the "
                "file after retry — it did NOT reach the user.",
                t0,
                record=record,
                extra={"action": "send_file", "channel": target, "delivery_status": status},
            )
        verified = status == "delivered"
        return self._ok(
            record, t0, note=f"send_file {status}", verified=verified,
            extra={"action": "send_file", "channel": target, "delivery_status": status},
        )

    def _resolve_workspace_file(
        self, raw_path: str
    ) -> tuple[Path | None, str | None]:
        """Resolve + validate ``raw_path``; return (resolved_path, None) or (None, error).

        Rejects (structured, never raises): a blank path, a path that does not
        exist, a non-regular file, a path that escapes the workspace dir
        (traversal / arbitrary absolute path), or a file over the size cap.
        """
        text = (raw_path or "").strip()
        if not text:
            return None, "blank file_path: provide a path to a workspace file."

        workspace = StackowlHome.workspace()
        try:
            workspace_root = workspace.resolve()
            # Relative names resolve UNDER the workspace (not the process CWD), so a
            # file the agent just produced in the workspace can be sent by bare name.
            candidate = Path(text)
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (workspace_root / text).resolve()
            )
        except OSError as exc:  # B5 — resolve can raise on a broken symlink loop
            log.tool.warning(
                "send_file.execute: path resolution failed",
                extra={"_fields": {"error": str(exc)}},
            )
            return None, f"could not resolve path {text!r}."

        # Containment: resolved must be the workspace root or a descendant of it.
        if workspace_root != resolved and workspace_root not in resolved.parents:
            return None, (
                f"file outside workspace: {text!r} does not reside under the "
                "StackOwl workspace directory — only workspace files can be sent."
            )

        if not resolved.exists():
            return None, (
                f"file not in workspace yet: {text!r} does not exist — produce it "
                "first (e.g. run a command that creates/downloads it into the "
                "workspace), then send it by name."
            )
        if not resolved.is_file():
            return None, f"not a regular file: {text!r} is not a file."

        try:
            size = resolved.stat().st_size
        except OSError as exc:  # B5 — stat failure (race / permissions)
            log.tool.warning(
                "send_file.execute: stat failed",
                extra={"_fields": {"error": str(exc)}},
            )
            return None, f"could not stat file {text!r}."
        if size > self._max_bytes:
            mb = self._max_bytes / (1024 * 1024)
            return None, (
                f"file too large: {size} bytes exceeds the {mb:.0f} MB limit — "
                "it cannot be sent over the channel."
            )

        return resolved, None

    async def _deliver(
        self,
        file_path: str,
        caption: str,
        target: str,
        trace_id: object,
        session_id: str,
    ) -> str:
        """Clamp + hand the file Notification to the S0 deliverer; never raises.

        Returns the transport ``DeliveryStatus``, or ``"deferred"`` when no
        deliverer is wired / the deliverer raises (self-healing, B5). The caption
        rides on the Notification's ``message`` field (empty string when none). The
        originating ``session_id`` resolves to the recipient ``chat_id`` (where the
        channel makes that valid — telegram private chats) so the file reaches THAT
        chat, not the adapter's shared mutable ``_last_chat_id``.
        """
        deliverer = get_services().proactive_deliverer
        if deliverer is None:
            log.tool.warning(
                "send_file._deliver: no proactive_deliverer wired — deferring",
                extra={"_fields": {"channel": target}},
            )
            return "deferred"

        notification = Notification(
            message=caption,
            urgency=clamp_agent_urgency("normal"),
            category=_CATEGORY,
            channel_name=target,
            idempotency_key=str(trace_id) if trace_id else None,
            file_path=file_path,
            target_chat_id=resolve_target_chat_id(target, session_id),
        )
        try:
            status = await deliverer.deliver(notification)
        except Exception as exc:  # B5 — deliverer is contracted not to raise; belt-and-braces.
            log.tool.error(
                "send_file._deliver: deliver raised — deferring",
                exc_info=exc,
                extra={"_fields": {"channel": target}},
            )
            return "deferred"

        if status == "failed":
            log.tool.warning(
                "send_file._deliver: deliver returned failed",
                extra={"_fields": {"channel": target}},
            )
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
        # success: None (default) ⇒ nothing to verify and the result is byte-identical
        # to pre-verification behavior; True ⇒ the bytes were observed reaching the
        # transport; False ⇒ accepted-but-queued (not yet delivered) so a downstream
        # decider does NOT treat it as a real delivery.
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "send_file.execute: exit",
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
        msg = f"send_file: {msg}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "send_file.execute: exit",
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
