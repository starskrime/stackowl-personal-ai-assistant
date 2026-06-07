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
from typing import Literal, get_args

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
        log.engine.debug(
            "[a2a-delegator] delegate: entry",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "from": from_owl,
                    "to": to_owl,
                    "sub_task_len": len(sub_task),
                    "timeout_s": self._timeout_seconds,
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
            response = await self._a2a_queue.receive(from_owl, timeout=self._timeout_seconds)
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
                        "timeout_s": self._timeout_seconds,
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
        # Ensure specialist task wrapped up cleanly (it should have, since it sent the reply).
        if not specialist_task.done():
            try:
                await asyncio.wait_for(specialist_task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError) as exc:
                log.engine.warning(
                    "[a2a-delegator] delegate: specialist task did not finish in time",
                    exc_info=exc,
                    extra={"_fields": {"trace_id": parent_state.trace_id, "to": to_owl}},
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
        )
        backend = AsyncioBackend(services=self._services)

        response_text = ""
        reply_status: str = "ok"
        reply_detail: str = ""
        try:
            final_state = await self._run_under_governor(backend, sub_state)
            response_text = "".join(chunk.content for chunk in final_state.responses)
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
            elif not response_text.strip():
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
            log.engine.warning(
                "[a2a-delegator] _run_specialist: cancelled",
                extra={"_fields": {"trace_id": parent_state.trace_id, "to": to_owl}},
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
