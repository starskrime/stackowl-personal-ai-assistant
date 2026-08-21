"""A2A delegation helper — Secretary dispatches sub-tasks to specialist owls.

The :class:`A2ADelegator` orchestrates the full request/response loop:

1. Send a ``request`` :class:`A2AMessage` to the specialist's mailbox.
2. Spawn a sibling :class:`AsyncioBackend` pipeline run for the specialist
   with the sub-task as ``input_text``; same ``trace_id`` for correlation.
3. When the specialist's pipeline terminates, post a ``response``
   :class:`A2AMessage` back to the caller's mailbox.
4. Caller awaits via :meth:`A2AQueue.receive` with a configurable timeout.
5. Timeouts/child errors log at warning/error level and return a structured
   ``A2AResult`` (status ``timeout``/``child_error``) — they never propagate as
   exceptions, so the caller degrades gracefully with an honest status.

Round-trip metadata (latency, trace_id continuity, mailbox depths) is logged
on every hop to support post-mortem analysis.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import A2ATimeoutError, StackOwlError
from stackowl.infra.observability import log
from stackowl.mcp._tool import sanitize_mcp_text as _sanitize
from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.owls.delegation_limits import GOVERNOR_ACQUIRE_TIMEOUT_SECONDS
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

DelegationStatus = Literal[
    "ok", "empty", "timeout", "child_error", "truncated", "refused",
    "cycle", "target_not_found", "off_topic",
]
# Derived from the Literal so the runtime whitelist can never drift from the type.
_KNOWN_STATUSES: frozenset[str] = frozenset(get_args(DelegationStatus))


class A2AResult(BaseModel):
    """Structured outcome of one delegation round-trip (replaces the bare ``str`` return).

    ``status`` is GOVERNOR-DECIDED from observed facts (exception / timeout / empty /
    final_state.errors) — never parsed from child output, so a child cannot fake a status
    to steer the recovery ladder. ``child_detail`` is sanitized untrusted data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DelegationStatus
    content: str = ""
    child_detail: str = ""
    resolved_owl: str = ""


async def settle_specialist_task(
    specialist_task: asyncio.Task[Any],
    *,
    trace_id: str,
    to_owl: str,
    timeout: float = 1.0,
) -> None:
    """Let the child's task object finish unwinding. Observation only; never raises.

    THIS IS NOT A TIMEOUT ON THE DELEGATION, and calling it one cost seven false
    alarms in a single day. It runs only AFTER ``receive`` has returned, so the reply
    is already in hand — this branch cannot indicate a delegation failure, by
    construction. Measured 2026-08-21: of 14 such log lines, 12 were followed by
    ``delegate: exit status=ok``, and ALL 14 carried ``duration_ms=0`` — meaning the
    specialist had replied BEFORE the parent began waiting, so its coroutine was still
    unwinding when the parent checked. The old WARNING therefore fired precisely when
    delegation was FASTEST.

    DEBUG, deliberately, against the usual rule that evidence belongs at INFO. That
    rule exists so a claim can be proven in production; nothing here is a claim. A
    WARNING that cannot distinguish a real problem from ordinary operation is not an
    alarm — it is noise competing with the alarms that are real, which is the same
    defect as a completion warning firing on the happy path.

    Raising the wait would not help: no wait is correct, because the parent already has
    what it asked for. How long the child takes to unwind afterwards is bookkeeping.
    """
    if specialist_task.done():
        return
    try:
        await asyncio.wait_for(specialist_task, timeout=timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        log.engine.debug(
            "[a2a-delegator] delegate: the child's task was still unwinding after its "
            "reply arrived — the delegation itself is unaffected",
            exc_info=exc,
            extra={"_fields": {"trace_id": trace_id, "to": to_owl,
                               "waited_s": timeout}},
        )
    except Exception as exc:  # noqa: BLE001 — the reply is already in hand
        # The child raised on its way out AFTER answering. That cannot be allowed to
        # turn a delivered delegation into a failed turn, so it is recorded and
        # swallowed — recover loudly, never hide, but never at the caller's expense.
        log.engine.warning(
            "[a2a-delegator] delegate: the child raised while finishing, AFTER its "
            "reply was received — the delegation stands",
            exc_info=exc,
            extra={"_fields": {"trace_id": trace_id, "to": to_owl}},
        )


class A2ADelegator:
    """Delegates sub-tasks to specialist owls and awaits typed responses."""

    def __init__(
        self,
        a2a_queue: A2AQueue,
        services: StepServices,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self._a2a_queue = a2a_queue
        self._services = services
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def timeout_for(self, to_owl: str) -> float:
        """How long to wait for ``to_owl`` — the limit that OWL is allowed to run.

        MEASURED 2026-08-19: 72 delegation timeouts, every one at 30.0s, including
        secretary → mailbutler twice that day. Against this hardware over 22,099
        provider calls — median 6.5s, p90 75.2s, p99 132.4s, and 27.5% of calls
        longer than 30s — a single call exceeds the old wait more than a quarter of
        the time, and a delegated turn is many calls plus tool work. Delegation was
        close to guaranteed to fail for any sub-task worth delegating.

        Meanwhile every owl manifest already declares ``timeout_seconds`` (400.0),
        and the delegator ignored it for its own constructor default. Two sources of
        truth for one limit, and the shorter one was not the owl's own.

        Raising the constant would only be a better guess about one machine. Asking
        the target owl is one source of truth, needs no new knob, and follows the
        owl automatically when it is reconfigured.

        Never raises: a lookup that fails costs the caller the OLD default, never
        the delegation.
        """
        registry = getattr(self._services, "owl_registry", None)
        if registry is None:
            return self._timeout_seconds
        try:
            manifest = registry.get(to_owl)
            configured = float(getattr(manifest, "timeout_seconds", 0) or 0)
        except Exception as exc:
            log.engine.warning(
                "[a2a-delegator] could not read the target owl's timeout — using "
                "the default wait",
                exc_info=exc, extra={"_fields": {"to": to_owl}},
            )
            return self._timeout_seconds
        # A zero or negative configured wait would abandon the child instantly,
        # which is worse than the bug this fixes.
        return configured if configured > 0 else self._timeout_seconds

    async def delegate(
        self,
        from_owl: str,
        to_owl: str,
        sub_task: str,
        parent_state: PipelineState,
    ) -> A2AResult:
        """Run a Secretary-to-specialist round trip and return a structured result.

        Returns an :class:`A2AResult` whose ``status`` is governor-decided from
        observed facts (timeout / exception / empty / child errors) — never parsed
        from child output text so the child cannot spoof a status.
        """
        # Resolved ONCE, here, so the logs report the wait actually used. Logging
        # the constructor default while waiting on the owl's own limit is the kind
        # of lying log line that made the 30s timeout hard to find at all.
        wait_s = self.timeout_for(to_owl)
        log.engine.debug(
            "[a2a-delegator] delegate: entry",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "from": from_owl,
                    "to": to_owl,
                    "sub_task_len": len(sub_task),
                    "timeout_s": wait_s,
                }
            },
        )

        request = A2AMessage.now(
            from_owl=from_owl,
            to_owl=to_owl,
            content=sub_task,
            message_type="request",
            trace_id=parent_state.trace_id,
        )
        self._a2a_queue.send(request)
        log.engine.debug(
            "[a2a-delegator] delegate: request sent",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "to": to_owl,
                    "queue_depth": self._a2a_queue.queue_depth(to_owl),
                }
            },
        )

        specialist_task = asyncio.create_task(
            self._run_specialist(from_owl=from_owl, to_owl=to_owl, sub_task=sub_task, parent_state=parent_state),
            name=f"a2a-specialist-{to_owl}",
        )

        t0 = time.monotonic()
        try:
            response = await self._a2a_queue.receive(from_owl, timeout=wait_s)
        except A2ATimeoutError as exc:
            specialist_task.cancel()
            log.engine.warning(
                "[a2a-delegator] delegate: timeout awaiting response",
                exc_info=exc,
                extra={
                    "_fields": {
                        "trace_id": parent_state.trace_id,
                        "from": from_owl,
                        "to": to_owl,
                        "timeout_s": wait_s,
                    }
                },
            )
            return A2AResult(status="timeout", resolved_owl=to_owl)
        except StackOwlError as exc:
            specialist_task.cancel()
            log.engine.error(
                "[a2a-delegator] delegate: receive failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": parent_state.trace_id, "from": from_owl, "to": to_owl}},
            )
            return A2AResult(status="child_error", resolved_owl=to_owl, child_detail=_sanitize(str(exc)))

        duration_ms = (time.monotonic() - t0) * 1000
        await settle_specialist_task(
            specialist_task, trace_id=parent_state.trace_id, to_owl=to_owl,
        )

        # Governor-decided status: prefer the child-reported status when present;
        # otherwise derive from observed facts (content present → ok, blank → empty).
        # Status is NEVER parsed from content text.
        status: DelegationStatus = (
            response.status  # type: ignore[assignment]
            if response.status in _KNOWN_STATUSES
            else ("empty" if not response.content.strip() else "ok")
        )
        log.engine.info(
            "[a2a-delegator] delegate: exit",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "from": from_owl,
                    "to": to_owl,
                    "duration_ms": duration_ms,
                    "status": status,
                    "response_len": len(response.content),
                    "trace_id_match": response.trace_id == parent_state.trace_id,
                }
            },
        )
        return A2AResult(
            status=status,
            content=response.content,
            child_detail=_sanitize(response.error or ""),
            resolved_owl=to_owl,
        )

    async def _run_under_governor(
        self,
        backend: AsyncioBackend,
        sub_state: PipelineState,
    ) -> PipelineState:
        """Run the specialist pipeline under the shared concurrency budget.

        Acquires a slot from the injected governor before ``backend.run`` and
        releases it in ``finally`` (via the slot context manager) so a crash
        never leaks a permit. When no governor is wired (early-stage tests), run
        ungated and log a warning rather than failing.
        """
        governor = self._services.delegation_governor
        if governor is None:
            log.engine.warning(
                "[a2a-delegator] _run_under_governor: no delegation_governor wired — "
                "running ungated",
                extra={"_fields": {"trace_id": sub_state.trace_id, "owl": sub_state.owl_name}},
            )
            return await backend.run(sub_state)
        # Bounded acquire: under acquire-while-holding saturation the child fails
        # fast (GovernorSaturatedError, a StackOwlError) — caught by _run_specialist,
        # which replies empty and frees the parent — instead of deadlocking.
        async with governor.slot(timeout=GOVERNOR_ACQUIRE_TIMEOUT_SECONDS):
            return await backend.run(sub_state)

    async def _run_specialist(
        self,
        *,
        from_owl: str,
        to_owl: str,
        sub_task: str,
        parent_state: PipelineState,
    ) -> None:
        """Run a sibling pipeline for the specialist and emit a response message."""
        log.engine.debug(
            "[a2a-delegator] _run_specialist: entry",
            extra={"_fields": {"trace_id": parent_state.trace_id, "to": to_owl}},
        )
        sub_state = parent_state.evolve(
            owl_name=to_owl,
            input_text=sub_task,
            responses=(),
            tool_calls=(),
            errors=(),
            pipeline_step="dispatch",
            # Delegated specialist sub-pipeline: no direct user channel binding
            # to deliver/answer a clarify, so default-deny regardless of the
            # parent's interactivity. Clarify must bubble through the parent.
            interactive=False,
            # E8-S0 — increment delegation depth exactly once per level. The
            # child-toolset exclusion (depth>0) and the S1 depth refusal read
            # this; it is the structural fork-bomb cap.
            delegation_depth=parent_state.delegation_depth + 1,
            # T3 — append this hop to the audit chain so every child state
            # carries the full ancestry (parent chain + its own owl name).
            delegation_chain=parent_state.delegation_chain + (to_owl,),
            # D1 §8.2 Break-A — the durable scope (task_id/durable_owner_id) is
            # carried by VALUE on sub_state (parent_state already holds the child
            # id) and stamped fresh inside backend.run's own TraceContext.start,
            # never via a .set() on the parent coroutine's ContextVar. evolve()
            # preserves task_id/durable_owner_id unless overridden — do NOT clear
            # them here.
        )
        backend = AsyncioBackend(services=self._services)

        response_text = ""
        reply_status: str = "ok"
        reply_detail: str = ""
        try:
            final_state = await self._run_under_governor(backend, sub_state)
            # FULL text (including any never-empty FLOOR chunk) rides up to the parent
            # as the honest "I couldn't complete this" detail.
            response_text = "".join(chunk.content for chunk in final_state.responses)
            # STATUS-DECIDING text EXCLUDES floor chunks: the self-heal never-empty
            # floor (is_floor=True) is the zero-content backstop, NOT a real delivery.
            # A floor-ONLY child therefore decides "empty" (honest failure), mirroring
            # critical_failure._has_usable_response — never a fake "ok" that would hide
            # the failure from the parent's re-route/recovery ladder.
            usable_text = "".join(
                chunk.content
                for chunk in final_state.responses
                if not getattr(chunk, "is_floor", False)
            )
            # Governor-decide the child outcome from observed facts — never from content.
            if final_state.errors:
                if any(e.startswith("budget:stop:") for e in final_state.errors):
                    reply_status = "truncated"
                else:
                    reply_status = "child_error"
                reply_detail = _sanitize("; ".join(final_state.errors))
                log.engine.warning(
                    "[a2a-delegator] _run_specialist: specialist reported errors",
                    extra={
                        "_fields": {
                            "trace_id": parent_state.trace_id,
                            "to": to_owl,
                            "reply_status": reply_status,
                            "errors": list(final_state.errors),
                        }
                    },
                )
            elif not usable_text.strip():
                reply_status = "empty"
        except StackOwlError as exc:
            log.engine.error(
                "[a2a-delegator] _run_specialist: sub-pipeline failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": parent_state.trace_id, "to": to_owl}},
            )
            reply_status = "child_error"
            reply_detail = _sanitize(str(exc))
        except asyncio.CancelledError:
            # D1 §9 cancel-survival — an a2a timeout cancels THIS asyncio task, but
            # for a DURABLE child we must NOT finalize the tasks row to 'failed':
            # the row stays running/recovering so startup recovery (or the next
            # turn) resumes it from its checkpoint. We deliberately re-raise WITHOUT
            # touching the durable store here. (CancelledError is BaseException-only,
            # so the durable runner's `except Exception` paths never see it either.)
            log.engine.warning(
                "[a2a-delegator] _run_specialist: cancelled — durable child (if any) "
                "left running/recovering for recovery",
                extra={"_fields": {
                    "trace_id": parent_state.trace_id, "to": to_owl,
                    "durable_task_id": parent_state.task_id,
                }},
            )
            raise

        reply = A2AMessage.now(
            from_owl=to_owl,
            to_owl=from_owl,
            content=response_text,
            message_type="response",
            trace_id=parent_state.trace_id,
            status=reply_status,
            error=reply_detail or None,
        )
        self._a2a_queue.send(reply)
        log.engine.debug(
            "[a2a-delegator] _run_specialist: exit",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "to": from_owl,
                    "response_len": len(response_text),
                }
            },
        )
