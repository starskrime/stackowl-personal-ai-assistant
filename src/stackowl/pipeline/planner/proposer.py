"""ToolProposer — fast-tier LLM proposes the minimal tool set for a goal (E2-S3).

Returns tool names validated by EXACT membership against the live catalog —
hallucinated names are dropped, NEVER fuzzy-matched. Any provider/parse failure
returns an empty set (the planner treats that as fail-open). Tool descriptions are
length-capped before being shown to the model (a cheap Catalog-Poisoning
mitigation; the hard boundary is owl∩ceiling regardless).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.providers.registry import ProviderRegistry

_DESC_CAP = 200


def _parse_names(text: str, valid: frozenset[str]) -> frozenset[str]:
    names: set[str] = set()
    try:
        data = json.loads(text)
        raw = data.get("tools") if isinstance(data, dict) else data
        if isinstance(raw, list):
            names = {n for n in raw if isinstance(n, str) and n in valid}
    except Exception:  # noqa: BLE001 — malformed LLM output expected
        names = set()
    if names:
        return frozenset(names)
    return frozenset(n for n in valid if n in text)  # fallback: exact catalog names verbatim in text


class ToolProposer:
    """Fast-tier LLM that proposes the minimal tool set needed for a goal.

    Validates proposed names by EXACT membership against the live catalog.
    Hallucinated names are dropped silently — NO fuzzy-matching. Any
    provider/parse failure returns an empty frozenset (fail-open; the
    calling planner decides what to do with an empty proposal).
    """

    def __init__(self, provider_registry: ProviderRegistry | None) -> None:
        self._providers = provider_registry

    async def propose(self, goal: str, catalog: list[tuple[str, str]]) -> frozenset[str]:
        """Return the minimal frozenset of tool names relevant to *goal*.

        Args:
            goal: The user's stated objective.
            catalog: ``[(name, description), ...]`` of all available tools.

        Returns:
            A ``frozenset[str]`` of EXACT catalog names the model selected,
            or an empty frozenset on any error / empty catalog / no registry.
        """
        log.engine.debug(
            "[planner] proposer.propose: entry",
            extra={"_fields": {"tools": len(catalog)}},
        )
        if self._providers is None or not catalog:
            return frozenset()
        valid = frozenset(name for name, _ in catalog)
        listing = "\n".join(f"- {name}: {desc[:_DESC_CAP]}" for name, desc in catalog)
        messages = [
            Message(
                role="system",
                content=(
                    "You select the MINIMAL set of tools needed to accomplish a goal. "
                    'Reply with ONLY a JSON object: {"tools": ["name", ...]} using exact '
                    "tool names from the provided list. Include nothing the goal does not need."
                ),
            ),
            Message(role="user", content=f"GOAL:\n{goal}\n\nTOOLS:\n{listing}"),
        ]
        try:
            provider, model = self._providers.get_with_cascade_and_model("fast")
            result = await provider.complete(messages, model=model)
        except Exception as exc:  # noqa: BLE001 — fail-open; planner decides
            log.engine.warning(
                "[planner] proposer.propose: provider failed — empty",
                exc_info=exc,
            )
            return frozenset()
        names = _parse_names(result.content or "", valid)
        log.engine.debug(
            "[planner] proposer.propose: exit",
            extra={"_fields": {"selected": len(names)}},
        )
        return names
