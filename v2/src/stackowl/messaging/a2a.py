"""A2A inter-owl messaging — A2AMessage and A2AQueue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import A2ATimeoutError
from stackowl.health.status import HealthStatus
from stackowl.infra.observability import log

_QUEUE_DEPTH_DEGRADED_THRESHOLD = 10


class A2AMessage(BaseModel):
    """A single inter-owl message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_owl: str
    to_owl: str
    content: str
    message_type: Literal["request", "response", "event"]
    trace_id: str
    timestamp: str  # ISO-8601 UTC

    @property
    def sent_at(self) -> datetime:
        """Spec-name alias for ``timestamp`` parsed back into a UTC datetime."""
        return datetime.fromisoformat(self.timestamp)

    @classmethod
    def now(
        cls,
        *,
        from_owl: str,
        to_owl: str,
        content: str,
        message_type: Literal["request", "response", "event"],
        trace_id: str,
    ) -> A2AMessage:
        """Convenience constructor that stamps the current UTC timestamp."""
        return cls(
            from_owl=from_owl,
            to_owl=to_owl,
            content=content,
            message_type=message_type,
            trace_id=trace_id,
            timestamp=datetime.now(UTC).isoformat(),
        )


class A2AQueue:
    """Per-recipient FIFO message queue for inter-owl communication (FR142)."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[A2AMessage]] = {}

    def _get_queue(self, owl_name: str) -> asyncio.Queue[A2AMessage]:
        if owl_name not in self._queues:
            self._queues[owl_name] = asyncio.Queue()
        return self._queues[owl_name]

    def send(self, msg: A2AMessage) -> None:
        """Enqueue a message for the recipient owl (non-blocking)."""
        log.engine.debug(
            "[a2a] send: entry",
            extra={"_fields": {"from": msg.from_owl, "to": msg.to_owl, "type": msg.message_type}},
        )
        self._get_queue(msg.to_owl).put_nowait(msg)
        log.engine.debug("[a2a] send: enqueued", extra={"_fields": {"to": msg.to_owl}})

    async def receive(self, owl_name: str, timeout: float = 30.0) -> A2AMessage:
        """Await the next message for owl_name, raising A2ATimeoutError on timeout."""
        log.engine.debug(
            "[a2a] receive: waiting",
            extra={"_fields": {"owl": owl_name, "timeout_s": timeout}},
        )
        try:
            msg = await asyncio.wait_for(self._get_queue(owl_name).get(), timeout=timeout)
        except TimeoutError:
            log.engine.warning(
                "[a2a] receive: timeout",
                extra={"_fields": {"owl": owl_name, "timeout_s": timeout}},
            )
            raise A2ATimeoutError(owl_name) from None
        log.engine.debug(
            "[a2a] receive: got message",
            extra={"_fields": {"owl": owl_name, "from": msg.from_owl}},
        )
        return msg

    def queue_depth(self, owl_name: str) -> int:
        """Return the number of pending messages for owl_name."""
        if owl_name not in self._queues:
            return 0
        return self._queues[owl_name].qsize()

    def queue_depths(self) -> dict[str, int]:
        """Return ``{owl_name: pending_count}`` for every active queue."""
        return {name: q.qsize() for name, q in self._queues.items()}

    @property
    def contributor_name(self) -> str:
        return "a2a_queue"

    async def health_check(self) -> HealthStatus:
        """Report ``degraded`` when any queue exceeds the depth threshold."""
        log.engine.debug("[a2a] health_check: entry")
        depths = self.queue_depths()
        total = sum(depths.values())
        max_depth = max(depths.values(), default=0)
        if max_depth > _QUEUE_DEPTH_DEGRADED_THRESHOLD:
            log.engine.warning(
                "[a2a] health_check: queue overloaded",
                extra={"_fields": {"max_depth": max_depth, "total": total}},
            )
            return HealthStatus(
                name=self.contributor_name,
                status="degraded",
                message=(f"Queue depth {max_depth} exceeds threshold (>{_QUEUE_DEPTH_DEGRADED_THRESHOLD})"),
                latency_ms=0.0,
            )
        log.engine.debug(
            "[a2a] health_check: exit",
            extra={"_fields": {"total": total, "max_depth": max_depth}},
        )
        return HealthStatus(
            name=self.contributor_name,
            status="ok",
            message=None,
            latency_ms=0.0,
        )
