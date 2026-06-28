"""Wire frames for the gateway<->core IPC.

Every frame is a frozen Pydantic model with a string ``type`` discriminator.
The set mirrors the existing in-process seams so the live-path code (``backend.run``
writing chunks, the receive loops dispatching ``IngressMessage``) maps onto frames
with no semantic change:

  gateway -> core : ingress, steer, stop, clarify_reply, query_running
  core -> gateway : chunk, send_text, progress_event, clarify_ask,
                    running_state, hello, restart_notice, goodbye
  either way      : ack

Frames carry only JSON-serialisable scalars (mirroring ``IngressMessage`` /
``ResponseChunk``, which already hold no callables or asyncio objects), so a
frame survives ``model_dump_json()`` -> newline -> ``model_validate_json()``
round-trip intact.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frame(BaseModel):
    """Base for all wire frames — frozen, reject unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class HelloFrame(_Frame):
    """Core -> gateway on (re)connect: announces a fresh core is ready."""

    type: Literal["hello"] = "hello"
    core_pid: int
    protocol_version: int = 1


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
    session_id: str
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


class SteerFrame(_Frame):
    """Gateway -> core: fold a mid-turn steering message into a running turn."""

    type: Literal["steer"] = "steer"
    request_id: str
    text: str


class StopFrame(_Frame):
    """Gateway -> core: request cooperative stop of a running turn."""

    type: Literal["stop"] = "stop"
    request_id: str


class QueryRunningFrame(_Frame):
    """Gateway -> core: is a turn running for this session? (authoritative check)."""

    type: Literal["query_running"] = "query_running"
    session_id: str
    query_id: str


class RunningStateFrame(_Frame):
    """Core -> gateway: answer to a QueryRunningFrame."""

    type: Literal["running_state"] = "running_state"
    query_id: str
    running: bool
    request_id: str | None = None


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
    session_id: str
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
    session_id: str
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
    | SteerFrame
    | StopFrame
    | QueryRunningFrame
    | RunningStateFrame
    | SendTextFrame
    | SendFileFrame
    | ProgressEventFrame
    | ClarifyAskFrame
    | ClarifyReplyFrame
    | ConsentRequestFrame
    | ConsentResponseFrame
    | AckFrame,
    Field(discriminator="type"),
]
