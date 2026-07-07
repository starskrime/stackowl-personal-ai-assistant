"""MemoryBridge ABC, NullMemoryBridge, and HealthReport — pluggable memory access."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from stackowl.infra.observability import log
from stackowl.memory.trust import Trust

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from stackowl.memory.models import MemoryRecord, StagedFact


@dataclass(frozen=True)
class HealthReport:
    """Result of a :meth:`MemoryBridge.health` probe."""

    name: str
    status: Literal["ok", "degraded", "down"]
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


class MemoryBridge(ABC):
    """Abstract memory access layer.

    The bridge serves two callers:

    * **Pipeline** (:mod:`stackowl.pipeline.steps.classify`) calls
      :meth:`retrieve` and :meth:`store` with raw strings — the legacy contract.
    * **Knowledge pipeline** (Epic 6 staged → committed flow) uses
      :meth:`stage`, :meth:`recall`, :meth:`delete`, and :meth:`list_staged`
      with structured :class:`StagedFact` / :class:`MemoryRecord` objects.
    """

    # --- legacy pipeline contract ---------------------------------------------------

    @abstractmethod
    async def retrieve(self, query: str, session_id: str) -> str:
        """Return relevant memory context as a string, or ``""`` if none."""
        ...

    @abstractmethod
    async def store(self, content: str, session_id: str, *, trust: Trust | None = None) -> None:
        """Persist content for future retrieval.

        ``trust`` overrides the default trust level for this source type.  When
        ``None`` (the default) the implementation uses its own default mapping —
        backward-compatible for all existing callers.
        """
        ...

    # --- knowledge-pipeline contract -------------------------------------------------

    @abstractmethod
    async def stage(self, fact: StagedFact) -> None:
        """Insert a fact into the staged queue (pre-promotion)."""
        ...

    @abstractmethod
    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        """Return committed facts matching ``query``, best first."""
        ...

    @abstractmethod
    async def delete(self, fact_id: str) -> None:
        """Delete a fact from staged and/or committed stores."""
        ...

    @abstractmethod
    async def list_staged(
        self, status: Literal["staged", "committed", "rejected"] = "staged"
    ) -> list[StagedFact]:
        """List staged facts filtered by status."""
        ...

    async def find_committed_by_prefix(self, prefix: str) -> StagedFact | None:
        """Find one committed fact whose ``fact_id`` starts with *prefix*.

        Reads ``committed_facts`` directly — unlike :meth:`list_staged`, which
        (for ``status="committed"``) only ever sees a residual ``staged_facts``
        row and misses facts that live solely in ``committed_facts``. Used by
        :func:`stackowl.commands.staged_helpers.find_staged_by_id` to resolve
        an id prefix for ``/memory forget``. Default no-op (``None``); concrete
        bridges backed by a real ``committed_facts`` table override this.
        """
        return None

    async def clear_session(self, session_id: str) -> int:
        """Delete all conversation staged facts for *session_id*.

        Returns the number of rows deleted.  Used by ``/reset`` to actually
        wipe session history (the previous no-op implementation returned a
        hard-coded success message while deleting nothing).
        Default implementation returns 0 (no-op); concrete bridges override.
        """
        return 0

    async def recent_conversation_turns(
        self, session_id: str, limit: int = 6, staged_before: str | None = None,
    ) -> list[StagedFact]:
        """Return last *limit* conversation staged facts for *session_id*, oldest-first.

        Used by ``classify`` to give the LLM short-term memory of the in-progress
        session even before the dream worker promotes facts to ``committed_facts``.
        ``staged_before`` is an optional ISO-8601 cutoff (the DreamWorker settle
        window); ``None`` keeps the default short-term-recall behaviour.
        Default implementation returns ``[]``; concrete bridges override.
        """
        return []

    async def health(self) -> HealthReport:
        """Probe bridge health. Concrete implementations override with real checks."""
        return HealthReport(name="memory.null", status="ok", details={}, latency_ms=0.0)


class NullMemoryBridge(MemoryBridge):
    """No-op implementation — short-circuits all operations to safe defaults.

    Used when memory is disabled or for unit-tests that don't exercise the
    real SQLite-backed path.
    """

    async def retrieve(self, query: str, session_id: str) -> str:
        log.memory.debug(
            "[memory] NullMemoryBridge.retrieve: noop — returning empty context",
            extra={"_fields": {"session_id": session_id, "query_len": len(query)}},
        )
        return ""

    async def store(self, content: str, session_id: str, *, trust: Trust | None = None) -> None:
        log.memory.debug(
            "[memory] NullMemoryBridge.store: noop",
            extra={"_fields": {"session_id": session_id, "content_len": len(content), "trust_override": trust}},
        )

    async def stage(self, fact: StagedFact) -> None:
        # 1. ENTRY
        log.memory.info(
            "[memory] NullMemoryBridge.stage: noop — fact discarded",
            extra={"_fields": {"fact_id": fact.fact_id, "source_type": fact.source_type}},
        )

    async def recall(self, query: str, limit: int = 10) -> list[MemoryRecord]:
        log.memory.debug(
            "[memory] NullMemoryBridge.recall: noop — returning []",
            extra={"_fields": {"query_len": len(query), "limit": limit}},
        )
        return []

    async def delete(self, fact_id: str) -> None:
        log.memory.debug(
            "[memory] NullMemoryBridge.delete: noop",
            extra={"_fields": {"fact_id": fact_id}},
        )

    async def list_staged(
        self, status: Literal["staged", "committed", "rejected"] = "staged"
    ) -> list[StagedFact]:
        log.memory.debug(
            "[memory] NullMemoryBridge.list_staged: noop — returning []",
            extra={"_fields": {"status": status}},
        )
        return []
