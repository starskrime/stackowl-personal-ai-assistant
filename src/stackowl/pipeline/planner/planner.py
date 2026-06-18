"""PreflightPlanner — compose the least-privilege task envelope (E2-S3).

Single verdict: returns a TRUSTWORTHY non-empty BoundsSpec, or None. There is no
degraded-but-non-None state — `restrict_to` keys off this same verdict, so a
garbage/empty plan can never hide tools (the Restrict-To-Decoupling self-DoS fix).
The result is the proposer's validated set UNIONed with mandatory discovery tools
(the escape hatch). If the proposer contributed nothing, an envelope of
discovery-only would hide the entire real toolset — so we return None (fail-open).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stackowl.authz.bounds import BoundsSpec
from stackowl.authz.enforcement import assert_task_narrowing_enforceable
from stackowl.exceptions import DomainError
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.pipeline.planner.proposer import ToolProposer

MANDATORY_DISCOVERY = frozenset({"tool_search", "tool_describe"})


class PreflightPlanner:
    def __init__(self, proposer: ToolProposer) -> None:
        self._proposer = proposer

    async def plan(
        self, goal: str, owl_bounds: BoundsSpec | None, catalog: list[tuple[str, str]]
    ) -> BoundsSpec | None:
        log.engine.debug("[planner] plan: entry", extra={"_fields": {"tools": len(catalog)}})
        try:
            selected = await self._proposer.propose(goal, catalog)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.engine.warning("[planner] plan: proposer raised — no envelope", exc_info=exc)
            return None
        if not selected:
            log.engine.info("[planner] plan: proposer empty — no envelope (fail-open)")
            return None
        candidate = BoundsSpec(tools=frozenset(selected | MANDATORY_DISCOVERY))
        try:
            assert_task_narrowing_enforceable(owl_bounds, candidate)
        except DomainError as exc:
            log.engine.warning("[planner] plan: envelope failed honesty guard — none", exc_info=exc)
            return None
        log.engine.info("[planner] plan: envelope set", extra={"_fields": {"tools": len(candidate.tools or ())}})
        return candidate
