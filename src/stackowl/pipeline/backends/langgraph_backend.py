"""LangGraphBackend — orchestrates the 8-step pipeline via LangGraph StateGraph.

Implements ``OrchestratorBackend`` (ARCH-113). Mirrors AsyncioBackend semantics:
every step error is captured in ``state.errors`` and the pipeline continues to
``deliver``. Wraps PipelineState in a TypedDict so the immutable Pydantic model
flows through LangGraph's mutable graph-state contract.
"""

from __future__ import annotations

import asyncio
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
from stackowl.infra import decision_ledger, recovery_context, retry_ledger, tool_outcome_ledger
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.pipeline import lesson_context as lc
from stackowl.pipeline.backends.base import OrchestratorBackend
from stackowl.pipeline.backends.langgraph_callbacks import LoggingCallback
from stackowl.pipeline.backends.shared import run_delivery_gate
from stackowl.pipeline.budget import human_wait as human_wait_ctx
from stackowl.pipeline.registry import PIPELINE_STEPS, StepFn
from stackowl.pipeline.services import StepServices, get_services, reset_services, set_services
from stackowl.pipeline.state import PipelineState, StepError
from stackowl.pipeline.step_error import format_step_error
from stackowl.pipeline.steps import deliver
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.supervisor import synthesize_floor


async def _deliver_with_surfacing(state: PipelineState) -> PipelineState:
    """Run the FR-11/FR-12 shared gate cascade (parity with AsyncioBackend — same
    seam, same call), then deliver.

    Services are read from the ambient pipeline-services context (set by ``run``),
    matching how every other step resolves its dependencies. The shared seam is
    self-healing and never raises, so deliver always runs afterwards.
    """
    surfaced = await run_delivery_gate(state, get_services())
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
        # Wall-clock turn start — the acceptance freshness clock (a fresh artifact's
        # mtime is compared against this). monotonic() is unsuitable (not epoch).
        # Parity with AsyncioBackend (FR-13 gap fix).
        wall_t0 = time.time()
        token = set_services(self._services)
        trace_token = TraceContext.start(
            state.session_id,
            trace_id=state.trace_id,
            interactive=state.interactive,
            channel=state.channel,
            reply_target=state.reply_target,
            delegation_depth=state.delegation_depth,
            delegation_chain=state.delegation_chain,
            delegation_profile=state.delegation_profile,
            owl_name=state.owl_name,
            creation_ceiling=state.creation_ceiling,
            task_id=state.task_id,
            durable_owner_id=state.durable_owner_id,
            retry_lineage_id=state.retry_lineage_id,
        )
        lesson_token = lc.bind()
        recovery_token = recovery_context.bind()
        retry_ledger_token = retry_ledger.bind()
        ledger_token = tool_outcome_ledger.bind()
        # ADR-7 — bind the per-turn DecisionLedger only when enabled (default ON; off
        # only if settings explicitly sets decision_ledger=False). Unbound ⇒
        # record_decision no-ops ⇒ byte-identical to the pre-ADR-7 path.
        _settings = self._services.settings
        decision_token = (
            decision_ledger.bind()
            if _settings is None or _settings.decision_ledger
            else None
        )
        human_wait_token = human_wait_ctx.bind()
        # Global interactive turn deadline — parity with AsyncioBackend (same
        # 2026-07 incident: a telegram turn hung 1670+s; lower-level timeouts
        # don't cover every hang). Interactive turns only; non-interactive runs
        # (goal_execution, parliament, delegation children, evolution) carry
        # their own budgets and must never be cut by this.
        # getattr-guarded: unit tests hand StepServices duck-typed settings
        # stubs without a `system` section — those (and settings=None) mean
        # "deadline disabled", never a crash.
        deadline_s: float = (
            getattr(getattr(_settings, "system", None),
                    "interactive_turn_timeout_s", 0.0)
            if state.interactive
            else 0.0
        )
        # Workstream B, Phase 5 — captured inside `finally` (retry_ledger is
        # still bound there); see AsyncioBackend's identical comment for why
        # this can't be read at the _capture_outcome call site itself.
        retry_event_count = 0
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
            if deadline_s > 0:
                try:
                    output: _LGState = await asyncio.wait_for(
                        compiled.ainvoke({"pipeline_state": state}, config=config),
                        timeout=deadline_s,
                    )
                    final = output.get("pipeline_state", state)
                except TimeoutError:
                    final = await self._deadline_floor(state, deadline_s)
            else:
                output = await compiled.ainvoke({"pipeline_state": state}, config=config)
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
            if decision_token is not None:
                # ADR-7 — persist this turn's decisions durably (cross-process /
                # restart-safe) BEFORE reset clears the ledger. Best-effort: a
                # persistence failure must NEVER break the turn (B5).
                _decisions = decision_ledger.get_decisions()
                if self._services.db_pool is not None and state.session_id and _decisions:
                    try:
                        from stackowl.pipeline.decision_store import TurnDecisionStore
                        await TurnDecisionStore(self._services.db_pool).save(
                            session_id=state.session_id,
                            trace_id=state.trace_id,
                            decisions=_decisions,
                        )
                    except Exception as exc:
                        log.engine.error(
                            "[langgraph_backend] run: decision persist failed (swallowed)",
                            exc_info=exc,
                            extra={"_fields": {"session_id": state.session_id}},
                        )
                decision_ledger.reset(decision_token)
            tool_outcome_ledger.reset(ledger_token)
            recovery_context.reset(recovery_token)
            _retry_events = retry_ledger.get_retry()
            if _retry_events:
                log.engine.info(
                    "[retry] turn summary",
                    extra={"_fields": {
                        "trace_id": state.trace_id,
                        "retry_lineage_id": state.retry_lineage_id,
                        "events": [
                            {"kind": e.kind, "provider": e.provider, "detail": e.detail,
                             "attempt_number": e.attempt_number}
                            for e in _retry_events
                        ],
                    }},
                )
            retry_event_count = len(_retry_events)
            retry_ledger.reset(retry_ledger_token)
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
        from stackowl.pipeline.backends.shared import _capture_outcome, _verify_turn_acceptance

        # FR-13 parity fix: previously LangGraphBackend never called
        # _verify_turn_acceptance, so acceptance was always None here — a declared/
        # derived expected_outcome on a LangGraph-run turn was never checked, unlike
        # AsyncioBackend. Mirrors AsyncioBackend.run()'s tail exactly.
        acceptance = await _verify_turn_acceptance(final, wall_t0, self._services)
        await _capture_outcome(
            final, total_ms, self._services,
            acceptance=acceptance, retry_event_count=retry_event_count,
        )
        return final

    async def _deadline_floor(self, state: PipelineState, deadline_s: float) -> PipelineState:
        """Interactive deadline expiry — mirror of AsyncioBackend's TimeoutError arm.

        The cancelled graph invocation took every intermediate step state with
        it; the pre-graph state + this error is everything that survives. The
        floor ADDS an honest chunk; the error STAYS in errors so task_outcomes
        records a real failure (classify_failure → "TimeoutError"). Unlike
        AsyncioBackend — where the timeout arm falls through to the gate +
        deliver code below it — the graph's own "deliver" node died with the
        cancellation, so the gate cascade and deliver.run are invoked here
        explicitly, in the same order.
        """
        error_msg = (
            f"deadline: TimeoutError: interactive turn exceeded "
            f"{deadline_s:.0f}s deadline"
        )
        log.engine.error(
            "[langgraph_backend] run: interactive turn deadline exceeded — cancelled",
            extra={"_fields": {"trace_id": state.trace_id, "deadline_s": deadline_s}},
        )
        floor_chunk = ResponseChunk(
            content=synthesize_floor(
                goal=state.input_text,
                error=f"turn cancelled after {deadline_s:.0f}s deadline",
                attempts=[],
                partial="",
            ),
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
            is_floor=True,
        )
        floored = state.evolve(
            responses=(*state.responses, floor_chunk),
            errors=(*state.errors, error_msg),
            step_errors=(*state.step_errors,
                         StepError(step="deadline", exc_type="TimeoutError",
                                   message=f"interactive turn exceeded {deadline_s:.0f}s deadline")),
        )
        surfaced = await run_delivery_gate(floored, self._services)
        try:
            return await deliver.run(surfaced)
        except Exception as exc:
            deliver_msg = format_step_error("deliver", exc)
            log.engine.error(
                "[langgraph_backend] run: deliver failed after deadline — %s",
                deliver_msg,
                exc_info=True,
                extra={"_fields": {"step": "deliver", "trace_id": state.trace_id}},
            )
            return surfaced.evolve(
                errors=(*surfaced.errors, deliver_msg),
                step_errors=(*surfaced.step_errors,
                             StepError(step="deliver", exc_type=type(exc).__name__,
                                       message=str(exc))),
            )

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
            if name == "triage":
                # C1 fix (final whole-branch review) — mirrors AsyncioBackend's
                # short-circuit. Task 7's manual "do it again" hook (triage.run)
                # already dispatched+delivered a retry via
                # RetryActuator.attempt_retry (which itself edits/sends the
                # answer) and stamped retry_dispatched=True. Routing straight to
                # END skips the remaining steps AND the "deliver" node (which
                # itself runs the delivery gate + deliver.run) — otherwise the
                # raw "do it again" text would flow through the rest of the
                # graph and produce a SECOND response.
                builder.add_conditional_edges("triage", self._route_after_triage)
            elif name == "execute":
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
    def _route_after_triage(lg_state: _LGState) -> str:
        """C1 fix — route straight to END when triage already dispatched a
        manual retry (see the comment at the ``add_conditional_edges`` call
        site above); otherwise continue to "dispatch" as before.
        """
        pipeline_state = lg_state.get("pipeline_state")
        if pipeline_state is not None and pipeline_state.retry_dispatched:
            return END
        return "dispatch"

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
