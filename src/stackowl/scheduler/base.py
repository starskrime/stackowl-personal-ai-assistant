"""JobHandler ABC and HandlerRegistry — scheduler handler contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from stackowl.infra.observability import log
from stackowl.scheduler.job import Job, JobResult

TriggerKind = Literal["seeded", "on_demand", "event"]


class JobHandler(ABC):
    """Abstract job handler — one subclass per job type."""

    @property
    @abstractmethod
    def handler_name(self) -> str: ...

    @property
    def trigger_kind(self) -> TriggerKind:
        """How this handler's jobs come to exist — declares its wiring contract.

        * ``"seeded"`` (DEFAULT) — expects a standing ``jobs`` row created by
          ``SchedulerAssembly`` seeding. The wiring audit treats a "seeded"
          handler with NO row as DANGLING: it is registered but the poll loop
          will never dispatch it.
        * ``"on_demand"`` — created by a user/tool action (e.g. the cronjob
          tool), so NO standing row is expected at boot.
        * ``"event"`` — fired by an event, not the poll loop.

        Defaulting to ``"seeded"`` is FAIL-LOUD on purpose: a new handler that
        forgets to seed itself AND forgets to declare ``on_demand``/``event``
        gets flagged by :func:`audit_scheduler_wiring`, so a dangling
        registered-but-unreachable handler can never silently ship.
        """
        return "seeded"

    @property
    def defer_under_load(self) -> bool:
        """Whether the scheduler should DEFER this job while a user turn is live.

        Defaults to ``False`` (every existing handler keeps running exactly as
        before). Heavy, CPU/IO-hungry background jobs (dream_worker, kuzu_sync,
        critic_scorer, reflection_writer) override to ``True`` so they yield the
        resource-constrained box to the foreground turn — the scheduler skips
        them while ``TurnRegistry`` reports active turns, up to a starvation cap
        (see :class:`JobScheduler`), then runs them anyway so background work is
        never indefinitely starved.
        """
        return False

    @abstractmethod
    async def execute(self, job: Job) -> JobResult: ...


class HandlerRegistry:
    """Process-level registry of job handlers. Handlers self-register at import time."""

    _instance: HandlerRegistry | None = None

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._source_map: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> HandlerRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def register(self, handler: JobHandler, source_name: str | None = None) -> None:
        self._handlers[handler.handler_name] = handler
        if source_name:
            self._source_map.setdefault(source_name, []).append(handler.handler_name)
        log.scheduler.info(
            "[scheduler] registry.register: handler registered",
            extra={"_fields": {"handler": handler.handler_name, "source": source_name}},
        )

    def unregister(self, handler_name: str) -> None:
        """Remove a handler from the registry. No-op if absent."""
        if handler_name in self._handlers:
            del self._handlers[handler_name]
            log.scheduler.info(
                "[scheduler] registry.unregister: handler removed",
                extra={"_fields": {"handler": handler_name}},
            )

    def unregister_by_source(self, source_name: str) -> int:
        """Remove all handlers registered under source_name. Returns count removed."""
        log.scheduler.debug(
            "[scheduler] registry.unregister_by_source: entry",
            extra={"_fields": {"source": source_name}},
        )
        names = self._source_map.pop(source_name, [])
        for name in names:
            self._handlers.pop(name, None)
        log.scheduler.debug(
            "[scheduler] registry.unregister_by_source: exit",
            extra={"_fields": {"source": source_name, "removed": len(names)}},
        )
        return len(names)

    def get(self, name: str) -> JobHandler | None:
        return self._handlers.get(name)

    def all(self) -> list[JobHandler]:
        return list(self._handlers.values())

    def list(self) -> list[JobHandler]:
        """Alias for :meth:`all` — naming preferred by Story 7.1 spec."""
        return self.all()
