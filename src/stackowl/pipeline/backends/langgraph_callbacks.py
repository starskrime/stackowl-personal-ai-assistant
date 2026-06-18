"""LangGraph callback handlers — emit JSONL-friendly logs for graph execution."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext


class LoggingCallback(BaseCallbackHandler):
    """Emit a 4-point log record for every LangGraph node execution.

    Bridges LangGraph's callback hooks (``on_chain_start`` / ``on_chain_end`` /
    ``on_chain_error``) into the structured ``log.engine`` channel so every
    node entry/exit/error becomes a JSONL record carrying ``trace_id`` and
    ``duration_ms`` — matching the AsyncioBackend's per-step logging.
    """

    raise_error: bool = False
    run_inline: bool = True

    def __init__(self) -> None:
        super().__init__()
        # run_id (UUID) -> (node_name, start_monotonic_s)
        self._starts: dict[UUID, tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_name(serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
        name = kwargs.get("name")
        if isinstance(name, str) and name:
            return name
        if serialized:
            sname = serialized.get("name")
            if isinstance(sname, str) and sname:
                return sname
            sid = serialized.get("id")
            if isinstance(sid, list) and sid:
                tail = sid[-1]
                if isinstance(tail, str):
                    return tail
        return "<unknown>"

    @staticmethod
    def _current_trace_id() -> str | None:
        value = TraceContext.get().get("trace_id")
        return value if isinstance(value, str) else None

    # ------------------------------------------------------------------
    # BaseCallbackHandler overrides
    # ------------------------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = self._node_name(serialized, kwargs)
        self._starts[run_id] = (name, time.monotonic())
        log.engine.debug(
            "[langgraph_backend] node: entry",
            extra={
                "_fields": {
                    "node": name,
                    "trace_id": self._current_trace_id(),
                    "run_id": str(run_id),
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                }
            },
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        name, t0 = self._starts.pop(run_id, ("<unknown>", time.monotonic()))
        duration_ms = (time.monotonic() - t0) * 1000
        log.engine.debug(
            "[langgraph_backend] node: exit",
            extra={
                "_fields": {
                    "node": name,
                    "trace_id": self._current_trace_id(),
                    "run_id": str(run_id),
                    "duration_ms": duration_ms,
                }
            },
        )

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        name, t0 = self._starts.pop(run_id, ("<unknown>", time.monotonic()))
        duration_ms = (time.monotonic() - t0) * 1000
        # 4-point logging rule + B5 boundary: never silent in error path.
        log.engine.error(
            "[langgraph_backend] node: error — %s: %s",
            type(error).__name__,
            error,
            extra={
                "_fields": {
                    "node": name,
                    "trace_id": self._current_trace_id(),
                    "run_id": str(run_id),
                    "duration_ms": duration_ms,
                    "error_type": type(error).__name__,
                }
            },
        )
