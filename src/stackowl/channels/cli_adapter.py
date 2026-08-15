"""CLIAdapter — Textual TUI channel adapter.

Production path (Commit D, plan: gleaming-finding-puppy.md): consumes a
:class:`TuiComponents` from :class:`TuiAssembly` and routes input/output
through the EventBus. Input arrives via the ``compose_submitted`` event
(published by :class:`StackOwlApp` when the user hits Enter); output is
published to the ``response_chunk`` event so the
:class:`UIStateCoordinator` can pump it into the
:class:`ConversationView`.

Backward-compat: if no ``tui_components`` / ``event_bus`` are supplied,
the adapter falls back to the legacy raw-``RichLog + Input`` mode for
test fixtures that don't want to bring up the whole TUI stack.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog

from stackowl.channels.base import ChannelAdapter
from stackowl.events.bus import EventBus
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.tui.assembly import TuiComponents

_MAX_CHUNK_LEN = 4000
_TRUNCATION_SUFFIX = "…"

_COMPOSE_EVENT = "compose_submitted"
_RESPONSE_EVENT = "response_chunk"


def _split_at_sentence(text: str, max_len: int) -> list[str]:
    """Split long text at sentence boundaries."""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    while len(text) > max_len:
        cut = text.rfind(". ", 0, max_len)
        if cut == -1:
            cut = max_len
        else:
            cut += 1
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


class _LegacyStackOwlApp(App[None]):
    """Minimal Textual app — RichLog + Input. Used in tests / fallback only."""

    CSS = """
    RichLog { height: 1fr; border: solid $primary; }
    Input   { dock: bottom; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()

    def compose(self) -> ComposeResult:
        yield RichLog(highlight=True, markup=True, wrap=True)
        yield Input(placeholder="Type a message…")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._input_queue.put_nowait(event.value)
        self.query_one(Input).clear()

    def write(self, text: str) -> None:
        self.query_one(RichLog).write(text)

    async def next_input(self) -> str:
        return await self._input_queue.get()


# Back-compat alias for old tests.
_StackOwlApp = _LegacyStackOwlApp


class CLIAdapter(ChannelAdapter):
    """Textual-based CLI channel adapter — 4-zone TUI when tui_components is given."""

    def __init__(
        self,
        session_key: str | None = None,
        *,
        tui_components: TuiComponents | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session_key = session_key or str(uuid.uuid4())
        self._trace_counter = 0
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()

        if tui_components is not None and event_bus is not None:
            # Production 4-zone mode.
            self._mode = "fullzone"
            self._tui: TuiComponents | None = tui_components
            self._event_bus: EventBus | None = event_bus
            self._app: App[None] = tui_components.app
            self._event_bus.subscribe(_COMPOSE_EVENT, self._on_compose_submitted)
        else:
            # Legacy fallback (tests, dry-run, etc.).
            self._mode = "raw"
            self._tui = None
            self._event_bus = None
            self._app = _LegacyStackOwlApp()

        log.cli.debug(
            "[cli] CLIAdapter.init: ready",
            extra={"_fields": {"session_key": self._session_key, "mode": self._mode}},
        )

    @property
    def channel_name(self) -> str:
        return "cli"

    def _next_request_id(self) -> str:
        """Mint a unique, non-empty request_id (= trace_id) for this session.

        The monotonic counter guarantees uniqueness within a session; the
        guard rejects an empty/blank session (which would mint a malformed
        ``cli--{n}`` id) so a degenerate id can't reintroduce cross-delivery
        once routing keys on request_id.
        """
        if not self._session_key or not self._session_key.strip():
            log.gateway.error(
                "[mint] cli request_id invalid: empty session_key",
                extra={"_fields": {"session_key": self._session_key}},
            )
            raise ValueError("empty/invalid request_id: empty session_key")
        self._trace_counter += 1
        rid = f"cli-{self._session_key[:8]}-{self._trace_counter}"
        if not rid:
            log.gateway.error("[mint] cli request_id invalid", extra={"_fields": {"rid": rid}})
            raise ValueError("empty/invalid request_id")
        return rid

    def _on_compose_submitted(self, payload: object) -> None:
        """EventBus callback — synchronous; enqueue for ``receive`` to pull."""
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("text", ""))
        if text:
            self._input_queue.put_nowait(text)

    async def receive(self) -> IngressMessage:
        if self._mode == "fullzone":
            text = await self._input_queue.get()
        else:
            # Legacy path — pull from the raw app's internal queue.
            assert isinstance(self._app, _LegacyStackOwlApp)
            text = await self._app.next_input()
        trace_id = self._next_request_id()
        log.cli.info(
            "[cli] receive: got input",
            extra={"_fields": {
                "session_key": self._session_key, "text_len": len(text), "trace_id": trace_id,
            }},
        )
        return IngressMessage(
            text=text,
            session_key=self._session_key,
            channel=self.channel_name,
            trace_id=trace_id,
            is_direct=True,  # ADR-D — the CLI is inherently a private 1:1 terminal.
        )

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        log.cli.info("[cli] send: streaming chunks", extra={"_fields": {"session_key": self._session_key}})
        buffer = ""
        chunk_idx = 0
        last_is_final = False
        last_owl = ""
        last_trace = ""
        async for chunk in chunks:
            # Live-progress chunks reach the terminal via the EventBus
            # (pipeline_step_changed → PipelineStrip), NOT as conversation text —
            # skip them here so they never render as a bubble or pollute the buffer.
            if getattr(chunk, "kind", "answer") == "progress":
                continue
            buffer += chunk.content
            if self._mode == "fullzone" and self._event_bus is not None:
                # Publish to EventBus → UIStateCoordinator → ConversationView.
                self._event_bus.emit(_RESPONSE_EVENT, {
                    "text": chunk.content,
                    "owl_name": chunk.owl_name,
                    "chunk_index": chunk_idx,
                    "trace_id": chunk.trace_id,
                    "is_final": chunk.is_final,
                    "actions": chunk.actions,
                })
                chunk_idx += 1
                last_is_final = chunk.is_final
                last_owl = chunk.owl_name
                last_trace = chunk.trace_id
            else:
                # Legacy raw mode.
                assert isinstance(self._app, _LegacyStackOwlApp)
                self._app.write(chunk.content)
        # Belt-and-suspenders: if the provider never flagged a final chunk,
        # emit an empty terminal marker so the active bubble still closes.
        if (
            self._mode == "fullzone"
            and self._event_bus is not None
            and chunk_idx > 0
            and not last_is_final
        ):
            self._event_bus.emit(_RESPONSE_EVENT, {
                "text": "",
                "owl_name": last_owl,
                "chunk_index": chunk_idx,
                "trace_id": last_trace,
                "is_final": True,
            })
        if self._mode == "raw" and buffer and not buffer.endswith("\n"):
            assert isinstance(self._app, _LegacyStackOwlApp)
            self._app.write("\n")
        log.cli.info(
            "[cli] send: exit",
            extra={"_fields": {"session_key": self._session_key, "total_len": len(buffer)}},
        )

    async def send_text(self, text: str) -> None:
        log.cli.debug(
            "[cli] send_text: entry",
            extra={"_fields": {"session_key": self._session_key, "text_len": len(text)}},
        )
        if self._mode == "fullzone" and self._event_bus is not None:
            self._event_bus.emit(_RESPONSE_EVENT, {
                "text": text, "owl_name": "system", "chunk_index": 0, "trace_id": "",
            })
        else:
            assert isinstance(self._app, _LegacyStackOwlApp)
            for part in _split_at_sentence(text, _MAX_CHUNK_LEN):
                self._app.write(part)
            self._app.write("\n")

    async def run(self) -> None:
        """Launch the Textual app — blocks until the user exits.

        In fullzone mode, starts the UIStateCoordinator before entering the
        Textual loop and stops it cleanly on exit.
        """
        log.cli.info(
            "[cli] CLIAdapter.run: starting",
            extra={"_fields": {"mode": self._mode}},
        )
        if self._mode == "fullzone" and self._tui is not None:
            # Start coordinator inside the running loop. App.run_async blocks
            # the gateway phase; coordinator pumps EventBus → Textual messages
            # in the background.
            await self._tui.coordinator.start()
            try:
                await self._app.run_async()
            finally:
                await self._tui.coordinator.stop()
        else:
            await self._app.run_async()
        log.cli.info("[cli] CLIAdapter.run: exit")


class HeadlessCliAdapter(ChannelAdapter):
    """The "cli" channel when the process has NO TERMINAL. Parks; never polls.

    WHY THIS EXISTS, measured on the live box 2026-08-14: the gateway sat at
    100.3% CPU — a whole core, continuously — and a py-spy profile put ~95% of it
    in Textual's Linux input driver (``process_selector_events`` 41.2%,
    ``run_input_thread`` 15.5%, and the parser tick behind them).

    The mechanism is a busy-wait on end-of-file. ``start.sh`` launches the gateway
    with ``nohup ... &``, so stdin is ``/dev/null``; a select() on /dev/null is
    ALWAYS ready because it returns EOF immediately, so the input thread wakes,
    reads nothing, and selects again without ever blocking. The TUI was not just
    useless there — writing escape codes into a redirected log and reading
    keystrokes from /dev/null — it was spending a core to be useless.

    THE CHANNEL STILL HAS TO EXIST. "cli" is registered with the clarify gateway
    and pre-registered for proactive delivery; removing it would turn a clarify
    question or a scheduled brief addressed to "cli" into a ChannelNotFoundError.
    So this keeps the name and the contract, and drops what it is handed — but
    visibly, at INFO, rather than silently.
    """

    def __init__(self, session_key: str | None = None) -> None:
        self._session_key = session_key or str(uuid.uuid4())
        #: Text this adapter had to drop for want of a terminal. Kept so a test
        #: can assert the drop happened, and so `dropped` is inspectable in a repl.
        self.dropped: list[str] = []
        self._parked = asyncio.Event()
        log.cli.info(
            "[cli] headless adapter: no terminal on stdin — the TUI is NOT started",
            extra={"_fields": {"session_key": self._session_key}},
        )

    @property
    def channel_name(self) -> str:
        return "cli"

    async def run(self) -> None:
        """Park until cancelled. NOT a poll loop — that would move the spin, not
        remove it, and would look identical from outside."""
        log.cli.debug("[cli] headless adapter.run: parked")
        await self._parked.wait()

    async def receive(self) -> IngressMessage:
        """Block forever. There is no terminal, so there is no user input.

        Returning an empty message instead would spin the gateway's turn loop —
        the same defect one layer up.
        """
        await self._parked.wait()
        raise AssertionError("unreachable — headless receive never resolves")

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        """Drain the stream and drop it. Draining matters: a producer writing into
        a stream nobody reads blocks once the queue fills."""
        text = "".join([c.content async for c in chunks])
        await self.send_text(text)

    async def send_text(self, text: str) -> object | None:
        self.dropped.append(text)
        log.cli.info(
            "[cli] headless adapter: dropped a message — no terminal attached",
            extra={"_fields": {"chars": len(text), "preview": text[:80]}},
        )
        return None
