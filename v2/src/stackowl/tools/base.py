"""Tool ABC and ToolResult — base contract for all pipeline tools (ARCH-94)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log


class ToolResult(BaseModel):
    """The output of a single tool execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    output: str
    error: str | None = None
    duration_ms: float


class ToolManifest(BaseModel):
    """Declarative metadata for a tool — used by ConsequentialActionGate and MCP adapters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, object]
    action_severity: Literal["read", "write", "consequential"] = "read"


class Tool(ABC):
    """Abstract base for all tools available to the pipeline (ARCH-94).

    execute() may raise — __call__ catches and wraps into a failed ToolResult.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, object]:
        """JSON Schema describing the tool's parameters."""
        ...

    @property
    def manifest(self) -> ToolManifest:
        """Return a ToolManifest built from this tool's declared metadata.

        Subclasses may override to set a non-default action_severity.
        """
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

    @abstractmethod
    async def execute(self, **kwargs: object) -> ToolResult: ...

    async def __call__(self, **kwargs: object) -> ToolResult:
        """Invoke execute() and wrap any unhandled exception into a failed ToolResult."""
        import time

        TestModeGuard.assert_not_test_mode(f"tool.{self.name}")
        log.tool.debug(
            "tool.__call__: entry",
            extra={"_fields": {"tool": self.name}},
        )
        t0 = time.monotonic()
        try:
            result = await self.execute(**kwargs)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.tool.error(
                "tool.__call__: unhandled exception — wrapping",
                exc_info=exc,
                extra={"_fields": {"tool": self.name, "duration_ms": duration_ms}},
            )
            result = ToolResult(success=False, output="", error=str(exc), duration_ms=duration_ms)
        log.tool.debug(
            "tool.__call__: exit",
            extra={"_fields": {"tool": self.name, "success": result.success, "duration_ms": result.duration_ms}},
        )
        return result
