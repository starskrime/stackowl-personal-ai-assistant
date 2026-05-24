"""OrchestratorBackend ABC — common interface for all pipeline backends (ARCH-113)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from stackowl.pipeline.state import PipelineState


class OrchestratorBackend(ABC):
    """Abstract pipeline execution backend.

    Concrete implementations: AsyncioBackend, LangGraphBackend (Epic 3).
    The rest of the system depends only on this interface (ARCH-113).
    """

    @abstractmethod
    async def run(self, state: PipelineState) -> PipelineState:
        """Execute the full 8-step pipeline and return the final state."""
        ...

    async def shutdown(self) -> None:  # noqa: B027
        """Gracefully shut down the backend. No-op by default."""
