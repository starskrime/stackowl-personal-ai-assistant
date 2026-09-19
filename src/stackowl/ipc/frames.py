"""Wire frames for the gateway<->core IPC.

Every frame is a frozen Pydantic model with a string ``type`` discriminator.
The set mirrors the existing in-process seams so the live-path code (``backend.run``
writing chunks, the receive loops dispatching ``IngressMessage``) maps onto frames
with no semantic change:

  gateway -> core : ingress, clarify_reply, tasks_enqueued
  core -> gateway : chunk, send_text, journal_event, clarify_ask,
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

#: Story 4.3 — bumped from 6 (adds ``TasksEnqueuedFrame``, a new frame shape,
#: same precedent as every prior bump: Spec 2.3/2.4/2.5/3.5/3.6). Both sides
#: refuse to talk when their Hello's ``protocol_version`` disagrees
#: (``runtime.hello.evaluate_hello``); this is the ONE place that number is
#: declared, so a future wire-shape change need only change this constant.
PROTOCOL_VERSION = 7


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

    Spec 2.4 — ``link_secret`` carries the per-boot secret (``runtime.link_auth``)
    that proves this Hello came from the core process the gateway itself
    spawned/inherited its env into. Optional because only core's OUTGOING Hello
    ever carries a real value (the gateway's own Hello, and `_core_frame_loop`'s
    local comparison-only Hello, never populate it — `evaluate_hello` never
    compares this field; `GatewayLink._route` checks it separately, before
    `evaluate_hello` runs). NEVER logged in full: the name ends in ``secret`` so
    the existing ``SensitiveFieldFilter``/``is_credential_name`` redacts it
    everywhere in structured ``_fields`` automatically.
    """

    type: Literal["hello"] = "hello"
    sender_pid: int
    protocol_version: int = PROTOCOL_VERSION
    highest_migration: int
    registry_digest: str
    link_secret: str | None = None


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


class JournalEventFrame(_Frame):
    """Core -> gateway: one committed journal row, pushed after commit (Spec 2.5).

    Replaces the dead ``ProgressEventFrame`` (nothing ever constructed one —
    Spec 2.5's Intent). Carries the full row rather than a bare cursor
    watermark (Design Notes: AC1 reads "core pushes THE EVENT", and carrying
    the row avoids a second DB round-trip on the gateway's hot/live path —
    only the catch-up path re-reads via ``journal.fanout.read_since``).

    The gateway reconstructs the typed ``attrs`` model via
    ``get_registry().get(frame.event_type).attrs_model.model_validate(frame.attrs)``
    before calling ``journal.narrate()`` — safe ONLY because Story 2.3's Hello
    registry-digest check already guarantees gateway and core share one
    identical registry (Boundaries & Constraints).
    """

    type: Literal["journal_event"] = "journal_event"
    cursor: int
    event_id: str
    event_type: str
    schema_version: int
    occurred_at: str
    actor_kind: str
    actor_id: str
    device_id: str | None = None
    target_kind: str
    target_id: str
    outcome: str
    attention: str | None = None
    intensity: str | None = None
    record_ref: dict[str, object] | None = None
    # Review fix — every sibling field on this frame is required; `attrs` had
    # been the one exception with a default, so a future core-side caller
    # that forgets `attrs=` would fail LATE at the gateway's
    # `model_validate({})` call instead of failing FAST at construction here.
    attrs: dict[str, object]
    trace_id: str | None = None
    duration_ms: int | None = None


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
    #: Story 3.6 -- the durable `question` needs_you item id bound to this
    #: clarify's blocking-mode wait (``ClarifyGateway.ask()``), when one was
    #: opened. Lets the gateway-side delivery (``GatewayLink._deliver_clarify``)
    #: register its own sent message against the item, so a later cross-surface
    #: ``needs_you.resolved`` can find and edit it. ``None`` for a turn-yield
    #: entry, an F-71 auto-resolved entry, or when ``db_pool`` is unwired --
    #: every one of those never opens an item (mirrors ``PendingClarify.
    #: needs_you_item_id``'s own docstring).
    needs_you_item_id: str | None = None


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
    #: Spec 3.5 — mirrors ``ConsentRequest.reply_target``: WHERE to ask, as
    #: opposed to WHICH CONVERSATION is asking (``session_key``). A frame that
    #: omits this (old-shape wire bytes, or the field default) decodes to
    #: ``None``; ``GatewayLink._handle_consent`` refuses rather than guessing a
    #: chat id from ``session_key``.
    reply_target: int | str | None = None
    #: Story 3.6 -- the already-open durable `approval` needs_you item id
    #: (``ConsentPolicy.request()`` opens it before ever awaiting a prompter,
    #: Story 3.3/AD-28). Lets the gateway-side ``TelegramConsentPrompter``
    #: register its own sent message against THIS item id (not just the
    #: opaque, per-tap ``rid``), so a later cross-surface ``needs_you.resolved``
    #: (a local timeout, the periodic expiry sweep, a future non-Telegram
    #: surface) can find and edit that exact message. ``None`` when
    #: ``db_pool`` is unwired (mirrors ``reply_target``'s own decode-to-None
    #: convention) -- never a reason to refuse the request.
    item_id: str | None = None
    category: str | None = None
    summary: str = ""
    allow_relaxation: bool = True


class ConsentResponseFrame(_Frame):
    """Gateway -> core: the user's consent decision (a ConsentScope value)."""

    type: Literal["consent_response"] = "consent_response"
    consent_id: str
    scope: str


class TasksEnqueuedFrame(_Frame):
    """Gateway -> core: "work is waiting, wake the loop now" (Story 4.3, AD-1).

    Deliberately payload-free — the durable ``tasks`` row IS the state (its
    ``command_id``/``command_type`` etc. already live in the database both
    processes share), so this frame carries nothing to keep in sync with
    that row's shape. Core's receipt calls ``TaskLoop.wake()`` (best-effort,
    never blocks — mirrors ``pipeline/durable/turn_task.py``'s existing
    in-process ``loop.wake()`` precedent), so a COMMAND task a gateway-role
    surface enqueues does not wait out the tick.
    """

    type: Literal["tasks_enqueued"] = "tasks_enqueued"


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
    | JournalEventFrame
    | ClarifyAskFrame
    | ClarifyReplyFrame
    | ConsentRequestFrame
    | ConsentResponseFrame
    | TasksEnqueuedFrame
    | AckFrame,
    Field(discriminator="type"),
]
