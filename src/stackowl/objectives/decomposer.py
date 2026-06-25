"""ObjectiveDecomposer — turn an objective intent into ordered sub-goals (1B).

This is the autonomous planner the prior audit found missing (obs 5995: "No
Autonomous Planner"). It asks the standard-tier model to break a standing
objective into a short ordered list of concrete, individually-actionable
sub-goals, each of which the driver later runs as one durable task.

Fail-safe by construction: any provider failure or empty/garbled reply degrades
to a single sub-goal that IS the whole objective, so a decomposition miss runs
the objective as one step rather than stranding it (mirrors the router's
fail-safe-to-act philosophy).
"""

from __future__ import annotations

import re
import time

from stackowl.infra.observability import log
from stackowl.providers.base import Message
from stackowl.providers.registry import ProviderRegistry

#: Decomposition is a reasoning task — use the standard tier, not fast.
_DECOMP_TIER = "standard"
_DECOMP_MAX_TOKENS = 512
_DECOMP_TEMPERATURE = 0.0
#: Cap the plan size — a v1 objective is a handful of concrete steps, not a
#: sprawling tree. Anything beyond this is truncated (logged by the caller).
_MAX_SUBGOALS = 12

#: Strip a leading ordered/bullet marker: "1.", "2)", "-", "*", "•".
_MARKER_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")


class ObjectiveDecomposer:
    """Decompose an objective's natural-language intent into ordered sub-goals."""

    def __init__(self, provider_registry: ProviderRegistry) -> None:
        self._provider_registry = provider_registry

    def _build_prompt(self, intent: str) -> str:
        """Compose the decomposition prompt (English glue; intent inlined)."""
        return (
            "Break the following objective into a short ordered list of concrete, "
            "individually-actionable steps. Each step must be a single action the "
            "assistant can carry out on its own in one turn (fetch, read, search, "
            "summarize, compute, write, notify). Order them so each builds on the "
            "previous. Output ONLY the steps, ONE per line, with no numbering, "
            "bullets, headers, or commentary. Use at most "
            f"{_MAX_SUBGOALS} steps; prefer fewer.\n\n"
            f"Objective: {intent}"
        )

    @staticmethod
    def _parse_subgoals(raw: str) -> list[str]:
        """Parse the model reply into an ordered list of sub-goal strings.

        Strips any leading numbering/bullet marker, drops blank lines, and caps
        at :data:`_MAX_SUBGOALS`. Language-neutral — no keyword/stopword lists.
        """
        subgoals: list[str] = []
        for line in (raw or "").splitlines():
            cleaned = _MARKER_RE.sub("", line).strip()
            if cleaned:
                subgoals.append(cleaned)
            if len(subgoals) >= _MAX_SUBGOALS:
                break
        return subgoals

    async def decompose(self, intent: str) -> list[str]:
        """Return ordered sub-goals for ``intent``; fail-safe to ``[intent]``."""
        log.engine.debug(
            "[objectives] decompose: entry",
            extra={"_fields": {"intent_preview": intent[:80]}},
        )
        prompt = self._build_prompt(intent)
        messages = [Message(role="user", content=prompt)]
        t0 = time.monotonic()
        try:
            provider = self._provider_registry.get_with_cascade(_DECOMP_TIER)
            result = await provider.complete(
                messages,
                model="",
                max_tokens=_DECOMP_MAX_TOKENS,
                temperature=_DECOMP_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 — never strand an objective
            log.engine.error(
                "[objectives] decompose: provider call failed — single-step fallback",
                exc_info=exc,
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [intent]

        subgoals = self._parse_subgoals(result.content)
        if not subgoals:
            log.engine.info(
                "[objectives] decompose: empty/garbled reply — single-step fallback",
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [intent]
        log.engine.info(
            "[objectives] decompose: exit",
            extra={
                "_fields": {
                    "intent_preview": intent[:80],
                    "subgoal_count": len(subgoals),
                    "latency_ms": (time.monotonic() - t0) * 1000,
                }
            },
        )
        return subgoals
