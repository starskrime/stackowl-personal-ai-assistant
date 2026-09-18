"""Wire frames for the gateway<->core IPC.

Every frame is a frozen Pydantic model with a string ``type`` discriminator.
The set mirrors the existing in-process seams so the live-path code (``backend.run``
writing chunks, the receive loops dispatching ``IngressMessage``) maps onto frames
with no semantic change:

  gateway -> core : ingress, clarify_reply
  core -> gateway : chunk, send_text, progress_event, clarify_ask,
                    restart_notice, goodbye
  either way      : hello, ack

Frames carry only JSON-serialisable scalars (mirroring ``IngressMessage`` /
``ResponseChunk``, which already hold no callables or asyncio objects), so a
frame survives ``model_dump_json()`` -> newline -> ``model_validate_json()``
round-trip intact.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackowl.commands.response import Action

#: Spec 2.3 — bumped from 1. Both sides refuse to talk when their Hello's
#: ``protocol_version`` disagrees (``runtime.hello.evaluate_hello``); this is the
#: ONE place that number is declared, so a future wire-shape change need only
#: change this constant.
PROTOCOL_VERSION = 2


class _Frame(BaseModel):
    """Base for all wire frames — frozen, reject unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class HelloFrame(_Frame):
    """Either direction, on (re)connect: announces a fresh peer is ready.

    Spec 2.3 — travels BOTH ways (gateway sends on accept, core sends after its
    DB pool opens), not core-only as before. Carries everything
    ``runtime.hello.evaluate_hello`` needs to refuse a half-upgraded link:
    ``protocol_version``, the highest applied migration number, and a digest of
    the registered journal event types (+ the attention-policy version).
    """

    type: Literal["hello"] = "hello"
    sender_pid: int
    protocol_version: int = PROTOCOL_VERSION
    highest_migration: int
    registry_digest: str


class GoodbyeFrame(_Frame):
    """Core -> gateway: a clean, intentional disconnect (e.g. shutdown)."""

    type: Literal["goodbye"] = "goodbye"
    reason: str = ""


class RestartNoticeFrame(_Frame):
    """Core -> gateway: 'I am about to exec-replace myself' (auto-restart).

    Lets the gateway start buffering inbound and show an operator-visible notice
    BEFORE the connection drops, so the gap reads as an intentional reload.
    """

    type: Literal["restart_notice"] = "restart_notice"
    reason: str = ""
    grace_seconds: float = 0.0


class IngressFrame(_Frame):
    """Gateway -> core: a raw inbound message (mirrors ``IngressMessage``)."""

    type: Literal["ingress"] = "ingress"
    text: str
    session_key: str
    channel: str
    trace_id: str
    chat_id: int | str | None = None
    is_reply: bool = False
    is_direct: bool = False  # ADR-D — carries the 1:1 vocative-routing gate across IPC.


class ChunkFrame(_Frame):
    """Core -> gateway: one streamed response fragment (mirrors ``ResponseChunk``).

    The stream close is carried as a ChunkFrame with ``is_final=True,
    chunk_index=-1`` — byte-identical to the in-process ``StreamWriter.close``
    sentinel — so the gateway-side reader breaks on exactly the same condition.
    """

    type: Literal["chunk"] = "chunk"
    content: str
    is_final: bool
    chunk_index: int
    trace_id: str
    owl_name: str
    duration_ms: float | None = None
    kind: Literal["answer", "progress"] = "answer"
    target: int | str | None = None
    is_floor: bool = False
    actions: tuple[Action, ...] = ()
    raw_keyboard: dict[str, object] | None = None
    display_suffix: str | None = None


class SendTextFrame(_Frame):
    """Core -> gateway: a proactive/out-of-band text to deliver on a channel."""

    type: Literal["send_text"] = "send_text"
    channel: str
    text: str
    target: int | str | None = None


class SendFileFrame(_Frame):
    """Core -> gateway: a file/image to upload on a channel.

    The split's core has no live channel adapter (the bot/terminal lives in the
    gateway), so a file upload — like ``send_text`` — must cross the socket and be
    performed by the gateway's real adapter. ``file_path`` is a path on the shared
    local filesystem (gateway and core run on the same host)."""

    type: Literal["send_file"] = "send_file"
    channel: str
    file_path: str
    caption: str | None = None
    target: int | str | None = None


class SendEphemeralFrame(_Frame):
    """Core -> gateway: send a muted, self-deleting probe text (e.g. telegram_canary).

    ``request_id`` correlates the gateway's reply (:class:`EphemeralSentFrame`)
    back to the core-side caller awaiting the real message_id.
    """

    type: Literal["send_ephemeral"] = "send_ephemeral"
    request_id: str
    channel: str
    text: str
    target: int | str


class EphemeralSentFrame(_Frame):
    """Gateway -> core: the real message_id for a SendEphemeralFrame (by request_id)."""

    type: Literal["ephemeral_sent"] = "ephemeral_sent"
    request_id: str
    message_id: int


class DeleteMessageFrame(_Frame):
    """Core -> gateway: delete a previously-sent ephemeral message.

    Fire-and-forget — mirrors the real adapter's own ``delete_message`` contract
    (a delete failure is cosmetic cleanup, never a delivery failure), so no reply
    frame round-trips back.
    """

    type: Literal["delete_message"] = "delete_message"
    channel: str
    target: int | str
    message_id: int


class ProgressEventFrame(_Frame):
    """Core -> gateway: a UI progress event (e.g. pipeline_step_changed) as a dict.

    The payload is the same dict the in-process EventBus carries to the TUI
    coordinator; the gateway re-emits it on its local bus for rendering.
    """

    type: Literal["progress_event"] = "progress_event"
    event: str
    payload: dict[str, object] = Field(default_factory=dict)


class ClarifyAskFrame(_Frame):
    """Core -> gateway: a tool is asking the user a clarifying question.

    ``channel`` routes the question to the originating adapter; ``choices`` (when
    non-empty) lets the gateway render selectable buttons (the answer round-trips
    as a :class:`ClarifyReplyFrame`).
    """

    type: Literal["clarify_ask"] = "clarify_ask"
    clarify_id: str
    session_key: str
    question: str
    trace_id: str
    channel: str = ""
    choices: tuple[str, ...] = ()
    target: int | str | None = None


class ClarifyReplyFrame(_Frame):
    """Gateway -> core: the user's answer to a pending clarify."""

    type: Literal["clarify_reply"] = "clarify_reply"
    clarify_id: str
    answer: str


class ConsentRequestFrame(_Frame):
    """Core -> gateway: the pipeline needs the user's consent for a tool.

    Mirrors :class:`stackowl.tools.consent.ConsentRequest` (all scalar fields) so
    the gateway can rebuild it and invoke the real per-channel consent prompter
    (e.g. Telegram inline buttons). The decision returns as a ConsentResponseFrame.
    """

    type: Literal["consent_request"] = "consent_request"
    consent_id: str
    channel: str
    tool_name: str
    session_key: str
    category: str | None = None
    summary: str = ""
    allow_relaxation: bool = True


class ConsentResponseFrame(_Frame):
    """Gateway -> core: the user's consent decision (a ConsentScope value)."""

    type: Literal["consent_response"] = "consent_response"
    consent_id: str
    scope: str


class AckFrame(_Frame):
    """Either direction: generic acknowledgement / deferred notice.

    ``status`` carries small control words (e.g. "deferred" when the core is
    quiescing and the gateway should buffer a new turn).
    """

    type: Literal["ack"] = "ack"
    ref: str = ""
    status: str = "ok"
    detail: str = ""


Frame = Annotated[
    HelloFrame
    | GoodbyeFrame
    | RestartNoticeFrame
    | IngressFrame
    | ChunkFrame
    | SendTextFrame
    | SendFileFrame
    | SendEphemeralFrame
    | EphemeralSentFrame
    | DeleteMessageFrame
    | ProgressEventFrame
    | ClarifyAskFrame
    | ClarifyReplyFrame
    | ConsentRequestFrame
    | ConsentResponseFrame
    | AckFrame,
    Field(discriminator="type"),
]
