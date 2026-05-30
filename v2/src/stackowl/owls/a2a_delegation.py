"""A2A delegation helper — Secretary dispatches sub-tasks to specialist owls.

The :class:`A2ADelegator` orchestrates the full request/response loop:

1. Send a ``request`` :class:`A2AMessage` to the specialist's mailbox.
2. Spawn a sibling :class:`AsyncioBackend` pipeline run for the specialist
   with the sub-task as ``input_text``; same ``trace_id`` for correlation.
3. When the specialist's pipeline terminates, post a ``response``
   :class:`A2AMessage` back to the caller's mailbox.
4. Caller awaits via :meth:`A2AQueue.receive` with a configurable timeout.
5. Timeouts log at warning level and return an empty string — they never
   propagate, so the Secretary can degrade gracefully.

Round-trip metadata (latency, trace_id continuity, mailbox depths) is logged
on every hop to support post-mortem analysis.
"""

from __future__ import annotations

import asyncio
import time

from stackowl.exceptions import A2ATimeoutError, StackOwlError
from stackowl.infra.observability import log
from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState


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
    ) -> str:
        """Run a Secretary-to-specialist round trip and return the response text.

        Returns the specialist's joined response chunks, or ``""`` on timeout.
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
            return ""
        except StackOwlError as exc:
            specialist_task.cancel()
            log.engine.error(
                "[a2a-delegator] delegate: receive failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": parent_state.trace_id, "from": from_owl, "to": to_owl}},
            )
            return ""

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

        log.engine.info(
            "[a2a-delegator] delegate: exit",
            extra={
                "_fields": {
                    "trace_id": parent_state.trace_id,
                    "from": from_owl,
                    "to": to_owl,
                    "duration_ms": duration_ms,
                    "response_len": len(response.content),
                    "trace_id_match": response.trace_id == parent_state.trace_id,
                }
            },
        )
        return response.content

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
        )
        backend = AsyncioBackend(services=self._services)

        response_text = ""
        try:
            final_state = await backend.run(sub_state)
            response_text = "".join(chunk.content for chunk in final_state.responses)
            if final_state.errors:
                log.engine.warning(
                    "[a2a-delegator] _run_specialist: specialist reported errors",
                    extra={
                        "_fields": {
                            "trace_id": parent_state.trace_id,
                            "to": to_owl,
                            "errors": list(final_state.errors),
                        }
                    },
                )
        except StackOwlError as exc:
            log.engine.error(
                "[a2a-delegator] _run_specialist: sub-pipeline failed",
                exc_info=exc,
                extra={"_fields": {"trace_id": parent_state.trace_id, "to": to_owl}},
            )
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
