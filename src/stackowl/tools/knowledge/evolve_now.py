"""evolve_now — trigger this owl's own DNA evolution on demand, mid-turn.

This is a THIN owl-tool wrapper around the EXISTING
:meth:`stackowl.owls.evolution.EvolutionCoordinator.evolve_one_owl_now`
(Story 3.1). That coordinator method was previously reachable ONLY as part of
the nightly batch job (``EvolutionCoordinator.execute``); this tool lets the
agent reach a single-owl evolution pass DURING a turn — so it can learn from
what just happened instead of waiting for the nightly cron.

REUSE, not reimplement: ``execute`` resolves the coordinator's deps off
:func:`get_services`, constructs the real :class:`EvolutionCoordinator`, and
``await``\\s ``coordinator.evolve_one_owl_now(owl_name)``. No evolution logic
lives here. ``evolve_one_owl_now`` forces the LLM-fallback path unconditionally
(FR-13/AD-5 — a single task can never meet DnaAttributor's 20-sample bar) and
routes through the SAME shadow-validation gate (``_checkpoint_validate_and_
promote``, Story 2.6) as the nightly batch — gated from day one, by
construction, no side door (AD-1/AD-3).

Severity (operator decision): ``read`` — like ``reflect_now``, it
analyzes/evolves the agent's OWN DNA from its own outcomes (not the user's
data and not an external side effect), so it is never consent-gated.
``toolset_group="knowledge"`` — beside the other self-improvement tools.

Self-healing (B5): missing deps (no db / provider / owl registry) or no owl
context for this turn degrade to a STRUCTURED failed ``ToolResult``, never a
raise; any coordinator exception is logged at ERROR and surfaced as a
structured failure (no hidden errors). A coordinator return of ``False``
(no deltas proposed, or the shadow gate rejected the mutation) is a NORMAL
outcome, not a tool failure — only a genuine exception is.
"""

from __future__ import annotations

import time

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.pipeline.services import get_services
from stackowl.tools.base import Tool, ToolManifest, ToolResult


class EvolveNowTool(Tool):
    """Trigger DNA evolution NOW for the current owl, on demand."""

    @property
    def name(self) -> str:
        return "evolve_now"

    @property
    def description(self) -> str:
        return (
            "Trigger DNA evolution NOW: run a single-task evolution pass over "
            "YOUR OWN personality traits (DNA) based on what just happened, "
            "instead of waiting for the nightly batch job. The proposal always "
            "goes through the same shadow-validation safety gate as the nightly "
            "batch before it can ship. Returns whether a mutation was promoted. "
            "LANE: evolve your OWN personality traits from what just happened. "
            "ANTI-LANE: do NOT use this to remember a fact (use memory) or to "
            "author a reusable procedure (use skill_manage) or to reflect on "
            "what went wrong (use reflect_now — that's a different, "
            "positive-only-learning subsystem)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="knowledge",
        )

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info("evolve_now.execute: entry", extra={"_fields": {}})

        services = get_services()
        # 2. DECISION — require the evolution subsystem deps; degrade structurally.
        missing = [
            label
            for label, dep in (
                ("db_pool", services.db_pool),
                ("provider_registry", services.provider_registry),
                ("owl_registry", services.owl_registry),
            )
            if dep is None
        ]
        if missing:
            return self._unavailable(", ".join(missing), t0)

        owl_name = TraceContext.get().get("owl_name")
        if not owl_name:
            return self._err(
                "evolve_now unavailable: no owl context for this turn", t0,
            )

        try:
            # 3. STEP — construct the REAL coordinator from services and call its
            # existing evolve_one_owl_now (REUSE; no reimplementation here).
            coordinator = EvolutionCoordinator(
                services.db_pool,  # type: ignore[arg-type]
                services.provider_registry,  # type: ignore[arg-type]
                services.owl_registry,  # type: ignore[arg-type]
            )
            promoted = await coordinator.evolve_one_owl_now(str(owl_name))
        except Exception as exc:  # B5 — degrade, never raise; no hidden errors.
            log.tool.error(
                "evolve_now.execute: coordinator failed — structured degradation",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name}},
            )
            return self._err(f"evolution failed: {type(exc).__name__}: {exc}", t0)

        # 4. EXIT — a False result (no deltas proposed / gate rejected) is a
        # NORMAL outcome, not a tool failure.
        output = "evolved:1" if promoted else "evolved:0"
        return self._ok(output, t0, owl=owl_name, promoted=promoted)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _ok(output: str, t0: float, *, owl: object, promoted: bool) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "evolve_now.execute: exit",
            extra={"_fields": {
                "success": True, "owl": owl, "promoted": promoted, "duration_ms": duration_ms,
            }},
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "evolve_now.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)

    @staticmethod
    def _unavailable(missing: str, t0: float) -> ToolResult:
        """Self-healing: a missing evolution subsystem degrades to a structured
        FAILED ToolResult (so the model knows nothing was evolved), never a raise."""
        msg = f"evolution subsystem not wired: missing {missing}"
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.warning(
            "evolve_now.execute: subsystem unavailable — structured degradation",
            extra={"_fields": {"missing": missing, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
