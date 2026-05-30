"""TraceContext — UUIDv4 trace/span IDs propagated automatically via contextvars."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any, NamedTuple
from uuid import uuid4


class _TraceToken(NamedTuple):
    trace: Token[str | None]
    span: Token[str | None]
    parent: Token[str | None]
    session: Token[str | None]
    interactive: Token[bool]
    channel: Token[str | None]


class TraceContext:
    """Stores and propagates trace/span IDs across async hops via contextvars."""

    _trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
    _span_id: ContextVar[str | None] = ContextVar("span_id", default=None)
    _parent_span_id: ContextVar[str | None] = ContextVar("parent_span_id", default=None)
    _session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
    _interactive: ContextVar[bool] = ContextVar("interactive", default=False)
    _channel: ContextVar[str | None] = ContextVar("channel", default=None)

    @classmethod
    def start(
        cls,
        session_id: str | None = None,
        *,
        trace_id: str | None = None,
        interactive: bool = False,
        channel: str | None = None,
    ) -> _TraceToken:
        """Set trace context for the current async task; return a token to reset later.

        ``trace_id`` is used verbatim when provided (the typical case: the channel
        adapter already minted one and we propagate it through the pipeline).
        When ``trace_id`` is None we mint a fresh UUID — useful for background
        jobs/scheduler handlers that start their own root trace.

        ``interactive`` and ``channel`` mirror the originating PipelineState so
        tools (which read TraceContext, not PipelineState) can tell whether a
        user is present to answer a clarify and on which channel to deliver it.
        FAIL-CLOSED: ``interactive`` defaults to False — a human is assumed
        absent unless a user-facing channel explicitly declares True.
        """
        return _TraceToken(
            trace=cls._trace_id.set(trace_id or str(uuid4())),
            span=cls._span_id.set(str(uuid4())),
            parent=cls._parent_span_id.set(None),
            session=cls._session_id.set(session_id),
            interactive=cls._interactive.set(interactive),
            channel=cls._channel.set(channel),
        )

    @classmethod
    def reset(cls, token: _TraceToken) -> None:
        """Restore previous context from a token returned by start()."""
        cls._trace_id.reset(token.trace)
        cls._span_id.reset(token.span)
        cls._parent_span_id.reset(token.parent)
        cls._session_id.reset(token.session)
        cls._interactive.reset(token.interactive)
        cls._channel.reset(token.channel)

    @classmethod
    @asynccontextmanager
    async def span(cls, name: str) -> AsyncIterator[None]:  # noqa: ARG003
        """Create a child span; restores previous span_id on exit."""
        old_span = cls._span_id.get()
        new_span_token = cls._span_id.set(str(uuid4()))
        parent_token = cls._parent_span_id.set(old_span)
        try:
            yield
        finally:
            cls._span_id.reset(new_span_token)
            cls._parent_span_id.reset(parent_token)

    @classmethod
    def get(cls) -> dict[str, Any]:
        """Return current trace context as a dict (safe to embed in log records)."""
        return {
            "trace_id": cls._trace_id.get(),
            "span_id": cls._span_id.get(),
            "parent_span_id": cls._parent_span_id.get(),
            "session_id": cls._session_id.get(),
            "interactive": cls._interactive.get(),
            "channel": cls._channel.get(),
        }
