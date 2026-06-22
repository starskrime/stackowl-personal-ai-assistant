"""LangGraphBackend — orchestrates the 8-step pipeline via LangGraph StateGraph.

Implements ``OrchestratorBackend`` (ARCH-113). Mirrors AsyncioBackend semantics:
every step error is captured in ``state.errors`` and the pipeline continues to
``deliver``. Wraps PipelineState in a TypedDict so the immutable Pydantic model
flows through LangGraph's mutable graph-state contract.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypedDict

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from stackowl.exceptions import InfrastructureError
from stackowl.infra import recovery_context, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.applied_lessons import surface_applied_lessons
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.langgraph_callbacks import LoggingCallback
from stackowl.pipeline.budget import human_wait as human_wait_ctx
from stackowl.pipeline.command_hint import surface_command_hint
from stackowl.pipeline.critical_failure import surface_critical_failure
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.overclaim_gate import surface_overclaim_gate
from stackowl.pipeline.recovery_summary import surface_recovery
from stackowl.pipeline.registry import PIPELINE_STEPS, StepFn
from stackowl.pipeline.services import StepServices, get_services, reset_services, set_services
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.turn_persist import persist_turn


async def _deliver_with_surfacing(state: PipelineState) -> PipelineState:
    """Surface a critical-step failure (shared helper), append applied-lesson
    explanation (pillar ④), then deliver.

    Services are read from the ambient pipeline-services context (set by ``run``),
    matching how every other step resolves its dependencies. Both surfacing helpers
    are self-healing and never raise, so deliver always runs afterwards.
    """
    # Applied-lesson annotation BEFORE critical-failure surfacing (see asyncio_backend
    # for the ordering rationale — prevents a learning claim on a failed turn).
    surfaced = await surface_applied_lessons(state)
    surfaced = await surface_recovery(surfaced)
    # Judge-independent gate: if a consequential/write action failed with no
    # success, REPLACE the (potentially dressed-up) draft with an honest floor
    # naming the failed capability. Runs BEFORE surface_critical_failure so the
    # critical-failure cascade sees an honest state (never hides behind a giveup).
    surfaced = await surface_consequential_giveup_floor(surfaced)
    # Overclaim delivery-gate (Task 6): structural gate that replaces a confident
    # non-floor draft with the honest floor when nothing was delivered and a tool
    # failed/bounced. Parity with AsyncioBackend. Never raises.
    surfaced = await surface_overclaim_gate(surfaced)
    surfaced = await surface_critical_failure(surfaced, get_services())
    # WS-D issue 3 — additive NL→command hint (+ routing-correction notice) on a
    # REAL answer. Gated by ui.command_hints (no-op when off); parity with
    # AsyncioBackend. Never raises.
    surfaced = await surface_command_hint(surfaced, get_services())
    # F088 — persist AFTER the honest floor band (parity with AsyncioBackend),
    # synchronously inside the ledger ContextVar binding established by run().
    # Floored turns record the user utterance only — never the dressed-up draft.
    await persist_turn(surfaced)
    return await deliver.run(surfaced)

try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    _HAS_ASYNC_SQLITE = True
except ImportError as _exc:  # pragma: no cover — sqlite saver is in default deps
    logging.getLogger("stackowl.engine").warning(
        "[langgraph_backend] AsyncSqliteSaver unavailable — falling back to MemorySaver: %s",
        _exc,
    )
    _HAS_ASYNC_SQLITE = False
    AsyncSqliteSaver = None  # type: ignore[assignment,misc]


class _LGState(TypedDict, total=False):
    """LangGraph state container — single key holds the immutable PipelineState."""

    pipeline_state: PipelineState


_NodeFn = Callable[[_LGState], Coroutine[Any, Any, _LGState]]


class LangGraphBackend(OrchestratorBackend):
    """LangGraph-powered backend for the 8-step pipeline.

    Construction is cheap — the graph is built once and reused across runs.
    The first ``run()`` lazily initialises the checkpointer (AsyncSqliteSaver
    when available, MemorySaver otherwise) and binds it into the compiled graph.
    """

    def __init__(
        self,
        *,
        services: StepServices | None = None,
        db_path: Path | None = None,
        use_memory_checkpoint: bool = False,
    ) -> None:
        self._services = services or StepServices()
        self._db_path = db_path
        self._use_memory_checkpoint = use_memory_checkpoint
        self._sqlite_conn: aiosqlite.Connection | None = None
        self._checkpointer: BaseCheckpointSaver[Any] | None = None
        self._compiled: Any | None = None  # CompiledStateGraph — no public type alias
        self._builder: StateGraph[_LGState, None, _LGState, _LGState] = self._build_graph_builder()

    # -- OrchestratorBackend ------------------------------------------------

    async def run(self, state: PipelineState) -> PipelineState:
        log.engine.debug(
            "[langgraph_backend] run: entry",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "session_id": state.session_id,
                    "total_steps": len(PIPELINE_STEPS) + 1,
                }
            },
        )
        t0 = time.monotonic()
        token = set_services(self._services)
        trace_token = TraceContext.start(
            state.session_id,
            trace_id=state.trace_id,
            interactive=state.interactive,
            channel=state.channel,
            reply_target=state.reply_target,
            delegation_depth=state.delegation_depth,
            delegation_chain=state.delegation_chain,
            owl_name=state.owl_name,
            creation_ceiling=state.creation_ceiling,
            task_id=state.task_id,
            durable_owner_id=state.durable_owner_id,
        )
        lesson_token = lc.bind()
        recovery_token = recovery_context.bind()
        ledger_token = tool_outcome_ledger.bind()
        human_wait_token = human_wait_ctx.bind()
        try:
            compiled = await self._ensure_compiled()
            # Isolate per-task checkpoints: a durable task gets its own thread so
            # its resume replays the right checkpoint. Falls back to the plain
            # session id (exact prior behavior) when no task_id is set.
            thread_id = (
                f"{state.session_id}::{state.task_id}"
                if state.task_id
                else state.session_id
            )
            config: dict[str, Any] = {
                "configurable": {"thread_id": thread_id},
                "metadata": {"trace_id": TraceContext.get().get("trace_id") or state.trace_id},
                "callbacks": [LoggingCallback()],
                "recursion_limit": 50,
            }
            output: _LGState = await compiled.ainvoke({"pipeline_state": state}, config=config)
            final = output.get("pipeline_state", state)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.engine.error(
                "[langgraph_backend] run: graph invocation failed — %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
                extra={
                    "_fields": {
                        "trace_id": state.trace_id,
                        "session_id": state.session_id,
                        "duration_ms": duration_ms,
                    }
                },
            )
            raise InfrastructureError(f"LangGraph backend invocation failed: {type(exc).__name__}: {exc}") from exc
        finally:
            _rec_events = recovery_context.get_recovery()
            if _rec_events:
                log.engine.info(
                    "[recovery] turn summary",
                    extra={"_fields": {
                        "trace_id": state.trace_id,
                        "events": [
                            {"kind": e.kind, "failed": e.failed,
                             "recovered_via": e.recovered_via, "user_visible": e.user_visible}
                            for e in _rec_events
                        ],
                    }},
                )
            human_wait_ctx.reset(human_wait_token)
            tool_outcome_ledger.reset(ledger_token)
            recovery_context.reset(recovery_token)
            lc.reset(lesson_token)
            TraceContext.reset(trace_token)
            reset_services(token)

        total_ms = (time.monotonic() - t0) * 1000
        log.engine.debug(
            "[langgraph_backend] run: exit",
            extra={
                "_fields": {
                    "trace_id": state.trace_id,
                    "total_ms": total_ms,
                    "error_count": len(final.errors),
                }
            },
        )
        # Parity with AsyncioBackend — Learning Commit 1 outcome capture.
        # Same best-effort contract: telemetry write failures never block return.
        # NOTE: LangGraph backend doesn't populate state.step_durations the way
        # AsyncioBackend does (its step boundaries are inside the compiled graph),
        # so step_durations may be empty here. quality_score still works.
        from stackowl.pipeline.backends.asyncio_backend import _capture_outcome

        await _capture_outcome(final, total_ms, self._services)
        return final

    async def shutdown(self) -> None:
        if self._sqlite_conn is None:
            return
        try:
            await self._sqlite_conn.close()
            log.engine.debug("[langgraph_backend] shutdown: sqlite connection closed")
        except Exception as exc:
            log.engine.warning(
                "[langgraph_backend] shutdown: sqlite close failed — %s: %s",
                type(exc).__name__,
                exc,
                extra={"_fields": {"error_type": type(exc).__name__}},
            )
        finally:
            self._sqlite_conn = None
            self._checkpointer = None
            self._compiled = None

    # -- Graph construction -------------------------------------------------

    def _build_graph_builder(self) -> StateGraph[_LGState, None, _LGState, _LGState]:
        """Build (but do not compile) the StateGraph with all 8 nodes wired."""
        builder: StateGraph[_LGState, None, _LGState, _LGState] = StateGraph(_LGState)
        for step_name, step_fn in PIPELINE_STEPS:
            builder.add_node(step_name, self._wrap_step(step_name, step_fn))  # type: ignore[call-overload]
        # Phase 2 #2 — the deliver node first surfaces a CRITICAL (execute) failure
        # to the user (shared helper, identical to AsyncioBackend) so no backend
        # diverges, then runs deliver. Self-healing; never raises into the graph.
        builder.add_node("deliver", self._wrap_step("deliver", _deliver_with_surfacing))  # type: ignore[call-overload]

        # Canonical sequence: triage → dispatch → classify → execute
        # → parliament_step (via Send) → consolidate → deliver → END.
        builder.set_entry_point(PIPELINE_STEPS[0][0])
        step_names = [name for name, _ in PIPELINE_STEPS]
        for i, name in enumerate(step_names):
            if name == "execute":
                builder.add_conditional_edges("execute", self._dispatch_parliament)
            elif name == "parliament_step":
                next_name = step_names[i + 1] if i + 1 < len(step_names) else "deliver"
                builder.add_edge("parliament_step", next_name)
            elif i + 1 < len(step_names):
                builder.add_edge(name, step_names[i + 1])
            else:
                builder.add_edge(name, "deliver")
        builder.add_edge("deliver", END)
        return builder

    @staticmethod
    def _dispatch_parliament(state: _LGState) -> list[Send]:
        """Fan-out edge from ``execute`` to ``parliament_step`` (single-owl stub)."""
        return [Send("parliament_step", state)]

    @staticmethod
    def _wrap_step(step_name: str, step_fn: StepFn) -> _NodeFn:
        """Wrap a canonical step fn into a LangGraph node fn (dict ↔ PipelineState).

        Mirrors AsyncioBackend's error-capture semantics: an exception in the
        step is appended to ``state.errors`` and the pipeline continues — never
        raised through the graph (which would short-circuit ``deliver``).
        """

        async def _node(lg_state: _LGState) -> _LGState:
            current = lg_state.get("pipeline_state")
            if current is None:
                log.engine.error(
                    "[langgraph_backend] node: missing pipeline_state",
                    extra={"_fields": {"step": step_name}},
                )
                raise InfrastructureError(f"LangGraph node '{step_name}' received empty state")
            current = current.evolve(pipeline_step=step_name)
            try:
                next_state = await step_fn(current)
                return {"pipeline_state": next_state}
            except Exception as exc:
                error_msg = format_step_error(step_name, exc)
                log.engine.error(
                    "[langgraph_backend] step failed — %s",
                    error_msg,
                    exc_info=True,
                    extra={"_fields": {"step": step_name, "trace_id": current.trace_id}},
                )
                # REACT-7/F092 — structured record in lockstep with the human string.
                return {"pipeline_state": current.evolve(
                    errors=(*current.errors, error_msg),
                    step_errors=(*current.step_errors,
                                 StepError(step=step_name, exc_type=type(exc).__name__, message=str(exc))),
                )}

        return _node

    # -- Checkpointer lifecycle --------------------------------------------

    async def _ensure_compiled(self) -> Any:
        if self._compiled is not None:
            return self._compiled
        self._checkpointer = await self._build_checkpointer()
        self._compiled = self._builder.compile(checkpointer=self._checkpointer)
        log.engine.debug(
            "[langgraph_backend] compiled",
            extra={"_fields": {"checkpointer": type(self._checkpointer).__name__}},
        )
        return self._compiled

    async def _build_checkpointer(self) -> BaseCheckpointSaver[Any]:
        if self._use_memory_checkpoint or not _HAS_ASYNC_SQLITE:
            reason = "explicit" if self._use_memory_checkpoint else "no_sqlite"
            log.engine.debug(
                "[langgraph_backend] checkpointer: MemorySaver",
                extra={"_fields": {"reason": reason}},
            )
            return MemorySaver()

        path = self._db_path or self._default_db_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = await aiosqlite.connect(str(path))
            saver = AsyncSqliteSaver(conn=self._sqlite_conn)
            await saver.setup()
            log.engine.debug(
                "[langgraph_backend] checkpointer: AsyncSqliteSaver",
                extra={"_fields": {"db_path": str(path)}},
            )
            return saver
        except Exception as exc:
            log.engine.warning(
                "[langgraph_backend] checkpointer: sqlite init failed, using memory — %s: %s",
                type(exc).__name__,
                exc,
                extra={"_fields": {"db_path": str(path), "error_type": type(exc).__name__}},
            )
            await self._close_failed_conn()
            return MemorySaver()

    async def _close_failed_conn(self) -> None:
        if self._sqlite_conn is None:
            return
        try:
            await self._sqlite_conn.close()
        except Exception as close_exc:
            log.engine.warning(
                "[langgraph_backend] checkpointer: close failed during fallback — %s",
                close_exc,
                extra={"_fields": {"error_type": type(close_exc).__name__}},
            )
        finally:
            self._sqlite_conn = None

    @staticmethod
    def _default_db_path() -> Path:
        from stackowl.paths import StackowlHome
        return StackowlHome.db_path()
