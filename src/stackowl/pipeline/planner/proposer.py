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
#: Directives are the user's own words and are already budget-capped by the
#: curated store (2,200 chars per file), but the planner may be handed more than
#: one file's worth. Capped here so a large profile cannot crowd the tool listing
#: out of a fast-tier context window — the listing is what the answer is drawn
#: from, so it must never be the part that gets truncated.
_DIRECTIVE_CAP = 1500


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

    async def propose(
        self,
        goal: str,
        catalog: list[tuple[str, str]],
        *,
        directives: str = "",
    ) -> frozenset[str]:
        """Return the minimal frozenset of tool names relevant to *goal*.

        Args:
            goal: The user's stated objective.
            catalog: ``[(name, description), ...]`` of all available tools.
            directives: ESC-54. The user's DURABLE standing instructions, so the
                model chooses BETWEEN tools the way the user has already said they
                want. Empty string = today's behaviour, byte for byte.

        Returns:
            A ``frozenset[str]`` of EXACT catalog names the model selected,
            or an empty frozenset on any error / empty catalog / no registry.

        DIRECTIVES DO NOT WIDEN ANYTHING. This method still validates every name
        by exact membership against *catalog*, and the planner still runs
        ``assert_task_narrowing_enforceable`` against owl ∩ ceiling afterwards.
        A directive can only change WHICH of the already-permitted tools gets
        picked — it is selection, never authorisation. That distinction is the
        reason this was safe to change: the failing case was a PLAN that omitted
        a tool the owl already held, not a plan that reached past its envelope.
        """
        log.engine.debug(
            "[planner] proposer.propose: entry",
            extra={"_fields": {"tools": len(catalog), "directive_chars": len(directives)}},
        )
        if self._providers is None or not catalog:
            return frozenset()
        valid = frozenset(name for name, _ in catalog)
        listing = "\n".join(f"- {name}: {desc[:_DESC_CAP]}" for name, desc in catalog)
        system = (
            "You select the MINIMAL set of tools needed to accomplish a goal. "
            'Reply with ONLY a JSON object: {"tools": ["name", ...]} using exact '
            "tool names from the provided list. Include nothing the goal does not need."
        )
        user = f"GOAL:\n{goal}\n\nTOOLS:\n{listing}"
        if directives.strip():
            # ESC-54. Stated as a CONSTRAINT ON THE CHOICE, not as extra context to
            # weigh: the measured failure was the model picking web_search +
            # web_fetch for a job search while a permanent user directive said to
            # use the browser instead, and while the owl already held
            # browser_navigate. "Minimal" alone gave it no reason to prefer one
            # capable tool over another, so it picked the obvious-sounding one.
            system += (
                " The user has STANDING DIRECTIVES about how work should be done. "
                "When two tools could both accomplish the goal, you MUST choose the "
                "one the directives call for, and you MUST NOT choose one they "
                "forbid. Directives never let you invent a tool that is not in the "
                "list."
            )
            user = (
                f"STANDING DIRECTIVES (durable, they outrank the phrasing of this "
                f"goal):\n{directives[:_DIRECTIVE_CAP]}\n\n{user}"
            )
        messages = [
            Message(role="system", content=system),
            Message(role="user", content=user),
        ]
        try:
            provider, model = self._providers.get_with_cascade("fast")
            # disable_thinking: an empty reply parses to frozenset(), so the planner
            # proposes no tools and logs it as an ordinary "selected: 0".
            result = await provider.complete(messages, model=model, disable_thinking=True)
        except Exception as exc:  # noqa: BLE001 — fail-open; planner decides
            log.engine.warning(
                "[planner] proposer.propose: provider failed — empty",
                exc_info=exc,
            )
            return frozenset()
        names = _parse_names(result.content or "", valid)
        # INFO, not DEBUG. This line is the ONLY evidence that a directive reached
        # the planner and what it changed; production runs at INFO, so a DEBUG line
        # could never close ESC-54's acceptance check no matter how much traffic ran.
        log.engine.info(
            "[planner] proposer.propose: exit",
            extra={"_fields": {
                "selected": len(names),
                "tools": sorted(names),
                "directives_supplied": bool(directives.strip()),
                "directive_chars": len(directives),
            }},
        )
        return names
