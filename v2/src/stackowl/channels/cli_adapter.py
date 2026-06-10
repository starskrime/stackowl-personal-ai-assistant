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
        session_id: str | None = None,
        *,
        tui_components: TuiComponents | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session_id = session_id or str(uuid.uuid4())
        self._trace_counter = 0
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()

        if tui_components is not None and event_bus is not None:
            # Production 4-zone mode.
            self._mode = "fullzone"
            self._tui = tui_components
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
            extra={"_fields": {"session_id": self._session_id, "mode": self._mode}},
        )

    @property
    def channel_name(self) -> str:
        return "cli"

    def _next_request_id(self) -> str:
        """Mint a unique, non-empty request_id (= trace_id) for this session.

        The monotonic counter guarantees uniqueness within a session; the
        guard rejects empty/invalid ids so a collision can't reintroduce
        cross-delivery once routing keys on request_id.
        """
        self._trace_counter += 1
        rid = f"cli-{self._session_id[:8]}-{self._trace_counter}"
        if not rid or self._trace_counter < 1:
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
                "session_id": self._session_id, "text_len": len(text), "trace_id": trace_id,
            }},
        )
        return IngressMessage(
            text=text,
            session_id=self._session_id,
            channel=self.channel_name,
            trace_id=trace_id,
        )

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        log.cli.info("[cli] send: streaming chunks", extra={"_fields": {"session_id": self._session_id}})
        buffer = ""
        chunk_idx = 0
        last_is_final = False
        last_owl = ""
        last_trace = ""
        async for chunk in chunks:
            buffer += chunk.content
            if self._mode == "fullzone" and self._event_bus is not None:
                # Publish to EventBus → UIStateCoordinator → ConversationView.
                self._event_bus.emit(_RESPONSE_EVENT, {
                    "text": chunk.content,
                    "owl_name": chunk.owl_name,
                    "chunk_index": chunk_idx,
                    "trace_id": chunk.trace_id,
                    "is_final": chunk.is_final,
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
            extra={"_fields": {"session_id": self._session_id, "total_len": len(buffer)}},
        )

    async def send_text(self, text: str) -> None:
        log.cli.debug(
            "[cli] send_text: entry",
            extra={"_fields": {"session_id": self._session_id, "text_len": len(text)}},
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
