"""update_plan — REPLACE the whole plan in one shot ({explanation, plan[]}).

A thin validator over the shared :class:`PlanStore` (the one plan slot also
mutated by ``todo``). It takes an ``explanation`` (a short why-this-plan note)
and a ``plan`` array, validates the single-in_progress invariant by
AUTO-CORRECTING (extra ``in_progress`` items are demoted to ``pending`` + logged
— never a hard reject mid-plan), replaces the WHOLE shared plan, and returns the
rendered plan plus the explanation.

An empty ``plan`` array clears the slot (a structured, non-error outcome).

Severity (operator decision): ``read`` — it mutates only an in-memory, ephemeral
plan used for context re-injection; no filesystem / external effect, no consent.
``toolset_group="planning"`` — the dedicated planning group shared with ``todo``.

Provenance / port-vs-build: BUILD (thin) — the reference is a ~22-line stateless
``{explanation, plan[]}`` validator enforcing ≤1 in_progress; its TUI specifics
are not ported. See ``_bmad-output/research/tool-port-analysis.md``
(E5 ``update_plan`` row).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.planning.store import VALID_STATUSES, PlanStore

if TYPE_CHECKING:  # pragma: no cover — typing-only
    pass


class UpdatePlanTool(Tool):
    """Replace the entire working plan at once (shared plan slot)."""

    def __init__(self, store: PlanStore | None = None) -> None:
        # Injected so ``update_plan`` and ``todo`` share ONE plan slot.
        self._store = store or PlanStore()

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def description(self) -> str:
        return (
            "REPLACE the whole working plan in one shot. Pass an 'explanation' "
            "(why this plan / what changed) and a 'plan' array of steps; this "
            "swaps the entire current plan. Each step is "
            "{id, content, status:pending|in_progress|completed|cancelled}; "
            "list order is priority; keep ONE step in_progress (extra ones are "
            "auto-demoted to pending). An empty 'plan' clears the plan. "
            "LANE: lay out or re-lay the full multi-step plan, with a rationale. "
            "ANTI-LANE: to tweak ONE item (add a step, mark one done) use todo "
            "instead. NOT memory (durable facts). NOT skills (how-to procedures)."
        )

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "explanation": {
                    "type": "string",
                    "description": "Short rationale for this plan / what changed.",
                },
                "plan": {
                    "type": "array",
                    "description": (
                        "The full list of steps that REPLACES the current plan. "
                        "Empty to clear."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique step id."},
                            "content": {
                                "type": "string",
                                "description": "Step description.",
                            },
                            "status": {
                                "type": "string",
                                "enum": sorted(VALID_STATUSES),
                                "description": "Step status.",
                            },
                        },
                        "required": ["id", "content"],
                    },
                },
            },
            "required": ["plan"],
        }

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="read",
            toolset_group="planning",
        )

    # ------------------------------------------------------------------ dispatch

    async def execute(self, **kwargs: object) -> ToolResult:
        t0 = time.monotonic()
        explanation = str(kwargs.get("explanation", "")).strip()
        raw_plan = kwargs.get("plan")
        # 1. ENTRY
        log.tool.info(
            "update_plan.execute: entry",
            extra={"_fields": {"has_explanation": bool(explanation)}},
        )

        # Hard-validate the 'plan' shape — a structured error, never a raise.
        if raw_plan is None or not isinstance(raw_plan, (list, tuple)):
            return self._err("update_plan requires a 'plan' array.", t0)

        try:
            # Self-healing: skip non-mapping junk rather than failing the call.
            items: list[Mapping[str, object]] = [
                it for it in raw_plan if isinstance(it, Mapping)
            ]
            # 2. DECISION — empty plan clears the slot (structured, not an error).
            if not items:
                self._store.clear()
                return self._render(
                    t0, explanation, cleared=True, item_count=0,
                )
            # 3. STEP — replace the WHOLE shared plan; the store auto-corrects the
            # single-in_progress invariant (demote extras, never reject).
            self._store.replace(items)
            return self._render(
                t0, explanation, cleared=False, item_count=len(self._store.read()),
            )
        except Exception as exc:  # self-healing — degrade, never raise.
            log.tool.error(
                "update_plan.execute: failed — degrading to structured error",
                exc_info=exc,
            )
            return self._err(f"{type(exc).__name__}: {exc}", t0)

    # ------------------------------------------------------------------- helpers

    def _render(
        self, t0: float, explanation: str, *, cleared: bool, item_count: int,
    ) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        body = (
            "(plan cleared)"
            if cleared
            else self._store.format_for_injection() or "(plan is empty)"
        )
        demoted = self._store.last_demoted()
        if not cleared and demoted:
            body += (
                f"\nnote: {len(demoted)} item(s) auto-demoted to pending "
                "(only one step may be in_progress at a time)."
            )
        output = f"{explanation}\n\n{body}" if explanation else body
        # 4. EXIT
        log.tool.info(
            "update_plan.execute: exit",
            extra={
                "_fields": {
                    "success": True,
                    "cleared": cleared,
                    "item_count": item_count,
                    "duration_ms": duration_ms,
                }
            },
        )
        return ToolResult(success=True, output=output, duration_ms=duration_ms)

    @staticmethod
    def _err(msg: str, t0: float) -> ToolResult:
        duration_ms = (time.monotonic() - t0) * 1000
        log.tool.info(
            "update_plan.execute: exit",
            extra={"_fields": {"success": False, "error": msg, "duration_ms": duration_ms}},
        )
        return ToolResult(success=False, output="", error=msg, duration_ms=duration_ms)
