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
    # Trusted, tool-declared consent category (e.g. "lock", "alarm", "destructive").
    # The consent gate keys always-ask exclusions off THIS, never off LLM-supplied
    # call args — the model must not be able to relax its own gating (E0-S1 / B2).
    consent_category: str | None = None
    # Toolset-group name for DNA-gated presentation (e.g. "code", "media", "home").
    # An owl's capability_profile lists group names; a tool joins the presented set
    # when its toolset_group is in that profile (ADR-11 / E1-S4). Distinct from
    # consent_category (which is about consent, not grouping).
    toolset_group: str | None = None
    # Capability tag groups tools that produce the same KIND of result, enabling
    # self-healing substitution: when a tool in a capability class fails, the
    # supervisor can route to a sibling with the same tag (W3 substitution actuator).
    capability_tag: str | None = None
    # D1 §6 — how tightly the tool's REAL-WORLD effect is coupled to our local
    # ledger commit. Decides definite-answer-vs-honest_uncertain after a durable
    # child times out / is recovered:
    #   "transactional"     — effect + ledger entry are atomic (L ⟺ E). "Committed
    #                         → done" is honest (e.g. a write to our own SQLite).
    #   "idempotent_keyed"  — effect is replay-safe under a key we own AND the
    #                         downstream contractually honors it (L ⟹ E).
    #   "unconfirmed"       — effect crosses a lossy-ack boundary (SMTP/POST/remote
    #                         FS/Telegram); L and E can diverge irreducibly.
    # None ⇒ undeclared. The resolver (delegate_task) treats undeclared write/
    # consequential tools as "unconfirmed" (fail-safe — never silently "safe").
    commit_coupling: Literal[
        "transactional", "idempotent_keyed", "unconfirmed"
    ] | None = None


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

    def consent_summary(self, **call_args: object) -> str | None:
        """Build a TRUSTED, bounded one-line summary of THIS call for the consent
        prompt, or ``None`` to fall back to the static :attr:`description`.

        The consent gate shows what a consequential action will actually DO so the
        user can approve meaningfully (e.g. ``execute_code`` renders the language +
        a bounded code digest + whether network is requested) — not just the
        generic tool description. Overrides MUST render from the tool's OWN trusted
        view of the validated args and stay BOUNDED (never echo unbounded raw LLM
        text); the gate truncates defensively regardless. Never raises.
        """
        return None

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
