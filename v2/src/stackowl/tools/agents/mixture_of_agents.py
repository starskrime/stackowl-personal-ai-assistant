"""MixtureOfAgentsTool — fan ONE hard question across the roster, then synthesize.

Two-layer Mixture-of-Agents over StackOwl's OWN provider roster (no vendor lock):

* **Layer 1 (fan-out):** asks ``ProviderRegistry.healthy_distinct()`` for the
  breaker-clean, distinct providers, then (in :mod:`moa_runner`) fans the question
  across them concurrently — each proposer under a per-proposer timeout, the batch
  gathered with ``return_exceptions=True``. A proposer that errors or times out is
  filtered out BEFORE synthesis (logged at ERROR, never hidden); the call survives
  on the survivors. Per-proposer cost is recorded by the PROVIDER itself inside
  ``provider.complete`` (E8-S0cost single recording site).
* **Layer 2 (synthesize):** the surviving positions go to
  :meth:`ParliamentSynthesizer.synthesize_positions` (positions-in / verdict-out —
  MoA never fabricates a fake ParliamentSession), collapsing them into one verdict
  with dissent preserved.

Self-healing rails ([[feedback_always_self_healing]] / [[feedback_no_hidden_errors]]):
fewer than two distinct healthy models → a structured ``insufficient_roster``
refusal (never a fake one-model "consensus"); all proposers failing →
``all_proposers_failed``; every failure is ERROR-logged AND surfaced in the result
(``degraded_ensemble`` flag + the ``failed`` proposer list). Never raises.

Severity ``read`` (queries models; no side effects). ``toolset_group`` ``agents``.
Provenance: HYBRID — a reference agent's 2-layer MoA shape (MoA paper) over our own
``ProviderRegistry`` + parliament synthesizer (see ``_bmad-output`` research, not src).
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, ValidationError

from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext
from stackowl.interaction.cost_pause import gate_or_continue
from stackowl.pipeline.services import get_services
from stackowl.tools.agents.moa_runner import run_ensemble
from stackowl.tools.agents.moa_schema import (
    MIXTURE_OF_AGENTS_DESCRIPTION,
    MIXTURE_OF_AGENTS_PARAMETERS,
)
from stackowl.tools.base import Tool, ToolManifest, ToolResult

_TOOLSET_GROUP = "agents"
_MIN_ROSTER = 2


class MixtureOfAgentsArgs(BaseModel):
    """Validated arguments for one ``mixture_of_agents`` invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    max_agents: int | None = None


class MixtureOfAgentsTool(Tool):
    """Consult several models on one hard question, then synthesize a verdict."""

    @property
    def name(self) -> str:
        return "mixture_of_agents"

    @property
    def description(self) -> str:
        return MIXTURE_OF_AGENTS_DESCRIPTION

    @property
    def parameters(self) -> dict[str, object]:
        return MIXTURE_OF_AGENTS_PARAMETERS

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group=_TOOLSET_GROUP,
        )

    # --------------------------------------------------------------- execute

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.tool.info(
            "mixture_of_agents.execute: entry",
            extra={"_fields": {"has_max_agents": "max_agents" in kwargs}},
        )

        try:
            args = MixtureOfAgentsArgs.model_validate(kwargs)
        except ValidationError as exc:
            log.tool.warning(
                "mixture_of_agents.execute: validation failed",
                extra={"_fields": {"errors": exc.error_count()}},
            )
            return self._err(f"mixture_of_agents: invalid arguments — {exc.errors()!r}", t0)

        services = get_services()
        registry = services.provider_registry
        if registry is None:
            log.tool.warning("mixture_of_agents.execute: no provider_registry wired — refusing")
            return self._ok(
                {
                    "status": "insufficient_roster",
                    "available": 0,
                    "detail": "no provider roster is available here; answer the question directly.",
                },
                t0,
                note="no provider roster available",
            )

        limit = args.max_agents if (args.max_agents and args.max_agents > 0) else None
        roster = registry.healthy_distinct(limit=limit)

        # 2. DECISION — refuse on a thin roster (no fake one-model "consensus").
        if len(roster) < _MIN_ROSTER:
            log.tool.info(
                "mixture_of_agents.execute: thin roster — refusing",
                extra={"_fields": {"available": len(roster), "min": _MIN_ROSTER}},
            )
            return self._ok(
                {
                    "status": "insufficient_roster",
                    "available": len(roster),
                    "detail": (
                        f"mixture_of_agents needs at least {_MIN_ROSTER} distinct healthy models; "
                        f"only {len(roster)} available — answer the question directly instead."
                    ),
                },
                t0,
                note="insufficient roster",
            )

        # E8-S0cost — soft per-turn cost pause via the ONE shared gate helper (B2:
        # same site as delegate_task). BEFORE the layer-1 fan-out (the expensive
        # parallel gather), if this turn's accumulated spend crossed the budget, it
        # ASKS the user (Continue/Stop). "Stop" aborts here — NO fan-out runs. The
        # helper/guard fail OPEN + are interactive-only, so a background run /
        # under-budget turn / disabled feature proceeds unchanged.
        ctx = TraceContext.get()
        trace_id = str(ctx.get("trace_id") or "")
        if not await gate_or_continue(services, action="fan-out"):
            log.tool.info(
                "mixture_of_agents.execute: cost pause — user chose Stop, aborting",
                extra={"_fields": {"trace_id": trace_id}},
            )
            return self._ok(
                {
                    "status": "cost_budget_stopped",
                    "detail": (
                        "stopped — this turn is over the per-turn cost budget and "
                        "the user chose not to continue; answer the question directly."
                    ),
                },
                t0,
                note="stopped — over the per-turn cost budget",
            )

        # 3. STEP — layer-1 fan-out + layer-2 synthesis (self-healing, never raises).
        # Per-proposer cost is recorded by the providers themselves inside
        # provider.complete (E8-S0cost single recording site) — no tracker threaded.
        record = await run_ensemble(
            registry=registry,
            roster=roster,
            question=args.question,
        )
        note = (
            f"synthesized from {record.get('consulted')} model(s)"
            if record.get("status") == "ok"
            else str(record.get("status"))
        )
        # 4. EXIT
        return self._ok(record, t0, note=note)

    # ---------------------------------------------------------------- helpers

    def _ok(self, record: dict[str, object], t0: float, *, note: str) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000.0
        log.tool.info(
            "mixture_of_agents.execute: exit",
            extra={"_fields": {"success": True, "status": record.get("status"), "duration_ms": duration_ms}},
        )
        payload = json.dumps({"note": note, "record": record}, ensure_ascii=False)
        return ToolResult(success=True, output=payload, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000.0
        log.tool.info(
            "mixture_of_agents.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
