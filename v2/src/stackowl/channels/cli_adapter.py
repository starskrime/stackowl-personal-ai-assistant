"""CLIAdapter — minimal Textual TUI: RichLog output + Input field.

NOT the full 4-zone composition (ships Epic 8). This is the architectural spine demo.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from textual.app import App, ComposeResult
from textual.widgets import Input, RichLog

from stackowl.channels.base import ChannelAdapter
from stackowl.gateway.scanner import IngressMessage
from stackowl.infra.observability import log
from stackowl.pipeline.streaming import ResponseChunk

_MAX_CHUNK_LEN = 4000
_TRUNCATION_SUFFIX = "…"


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


class _StackOwlApp(App[None]):
    """Minimal Textual app — RichLog on top, Input at the bottom."""

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


class CLIAdapter(ChannelAdapter):
    """Textual-based CLI channel adapter."""

    def __init__(self, session_id: str | None = None) -> None:
        self._session_id = session_id or str(uuid.uuid4())
        self._app = _StackOwlApp()
        self._trace_counter = 0
        log.cli.debug(
            "[cli] CLIAdapter: init",
            extra={"_fields": {"session_id": self._session_id}},
        )

    @property
    def channel_name(self) -> str:
        return "cli"

    async def receive(self) -> IngressMessage:
        text = await self._app.next_input()
        self._trace_counter += 1
        trace_id = f"cli-{self._session_id[:8]}-{self._trace_counter}"
        log.cli.debug(
            "[cli] receive: got input",
            extra={"_fields": {"session_id": self._session_id, "text_len": len(text), "trace_id": trace_id}},
        )
        return IngressMessage(
            text=text,
            session_id=self._session_id,
            channel=self.channel_name,
            trace_id=trace_id,
        )

    async def send(self, chunks: AsyncIterator[ResponseChunk]) -> None:
        log.cli.debug("[cli] send: streaming chunks", extra={"_fields": {"session_id": self._session_id}})
        buffer = ""
        async for chunk in chunks:
            buffer += chunk.content
            self._app.write(chunk.content)
        if buffer and not buffer.endswith("\n"):
            self._app.write("\n")
        log.cli.debug(
            "[cli] send: exit",
            extra={"_fields": {"session_id": self._session_id, "total_len": len(buffer)}},
        )

    async def send_text(self, text: str) -> None:
        log.cli.debug(
            "[cli] send_text: entry",
            extra={"_fields": {"session_id": self._session_id, "text_len": len(text)}},
        )
        for part in _split_at_sentence(text, _MAX_CHUNK_LEN):
            self._app.write(part)
        self._app.write("\n")

    async def run(self) -> None:
        """Launch the Textual app — blocks until the user exits."""
        await self._app.run_async()
