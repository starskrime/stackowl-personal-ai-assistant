"""ToolPresentation — DNA-gated presented-set selection (ADR-11 / E1-S4).

Resolves which tools an owl sees in a given turn from the full catalog:

    always-present (tool_search/describe)
      ∪ guaranteed base set (read-only essentials)
      ∪ owl pins (manifest.tools)
      ∪ hydrated tools (selected via tool_search this/last turn)
      ∪ profile-group tools (manifest.toolset_group ∈ owl.capability_profile)

capped at a hard limit with a deterministic priority order. Base + always-present
are NEVER evicted by the cap (self-heal: an owl always has a usable toolset, and
no first-party base tool is ever hidden). Overflow beyond the cap stays reachable
only through tool_search — that is the whole point of the meta-tool.

BUILD (StackOwl-native, no source to port).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:
    from stackowl.tools.base import Tool

__all__ = ["PresentationConfig", "ToolPresentation"]

# Operator vote: ~25. Phase B grew the non-evictable base set by 3 (the
# self-improvement trio), which would otherwise eat into the per-turn budget and
# crowd a full profile group (e.g. the 25-tool browser group) past the cap. Bump
# the cap by the same 3 so base growth does NOT shrink the discretionary headroom
# an owl profile already had (a browser owl still sees its core snapshot/click).
_DEFAULT_CAP = 28
# Guaranteed base set — read-only/foundation essentials every owl always has.
# Phase B: the self-improvement trio (skill_manage / reflect_now /
# synthesize_skills) joins the base set so EVERY owl can reach self-learning +
# gap-analysis/skill-build mid-turn (not only the nightly scheduler). The
# consequential ones (skill_manage, synthesize_skills) are still consent-gated at
# dispatch — surfacing them does not bypass consent.
_DEFAULT_BASE = frozenset({
    "read_file", "write_file", "shell", "web_fetch",
    "skill_manage", "reflect_now", "synthesize_skills",
})
_DEFAULT_ALWAYS = frozenset({"tool_search", "tool_describe"})


@dataclass(frozen=True)
class PresentationConfig:
    """Tunable presentation policy (cap + the always/base membership sets)."""

    cap: int = _DEFAULT_CAP
    base_tools: frozenset[str] = _DEFAULT_BASE
    always_present: frozenset[str] = _DEFAULT_ALWAYS


class ToolPresentation:
    """Selects the per-turn presented tool set from the full catalog."""

    def __init__(self, config: PresentationConfig | None = None) -> None:
        self._cfg = config or PresentationConfig()

    def select(
        self,
        *,
        all_tools: list[Tool],
        profile: list[str] | None,
        pins: list[str] | None,
        hydrated: set[str] | None,
    ) -> list[Tool]:
        """Return the ordered, capped presented set (deterministic, self-healing)."""
        cfg = self._cfg
        by_name = {t.name: t for t in all_tools}
        profile_groups = {g for g in (profile or []) if isinstance(g, str)}
        pin_names = {p for p in (pins or []) if isinstance(p, str)}
        hydrated_names = hydrated or set()

        # 1. ENTRY
        log.tool.debug(
            "[presentation] select: entry",
            extra={"_fields": {
                "catalog": len(all_tools), "groups": len(profile_groups),
                "pins": len(pin_names), "hydrated": len(hydrated_names), "cap": cfg.cap,
            }},
        )

        # Non-evictable tier: always-present + base (self-heal — never hidden by cap).
        guaranteed = sorted(
            n for n in (cfg.always_present | cfg.base_tools) if n in by_name
        )

        # Discretionary tiers, highest priority first: pins → hydrated → group tools.
        # Each tier sorted by name for a total, reproducible order.
        guaranteed_set = set(guaranteed)
        pins_tier = sorted(n for n in pin_names if n in by_name and n not in guaranteed_set)
        taken = guaranteed_set | set(pins_tier)
        hydrated_tier = sorted(n for n in hydrated_names if n in by_name and n not in taken)
        taken |= set(hydrated_tier)
        group_tier = sorted(
            n for n, t in by_name.items()
            if t.manifest.toolset_group in profile_groups and n not in taken
        )

        # Assemble: guaranteed first (never dropped), then fill discretionary tiers
        # in priority order until the cap is reached.
        ordered_names = list(guaranteed)
        budget = max(cfg.cap - len(ordered_names), 0)
        for tier in (pins_tier, hydrated_tier, group_tier):
            if budget <= 0:
                break
            take = tier[:budget]
            ordered_names.extend(take)
            budget -= len(take)

        selected = [by_name[n] for n in ordered_names]
        # 4. EXIT — surface how many discretionary tools the cap dropped, so an
        # operator can spot owls hitting the ceiling (they need a tighter profile).
        discretionary = len(pins_tier) + len(hydrated_tier) + len(group_tier)
        dropped = discretionary - (len(selected) - len(guaranteed))
        log.tool.debug(
            "[presentation] select: exit",
            extra={"_fields": {"presented": len(selected), "guaranteed": len(guaranteed), "dropped": dropped}},
        )
        return selected
