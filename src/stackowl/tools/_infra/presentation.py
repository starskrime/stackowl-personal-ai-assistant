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
# H4 adds tool_build (self-extension) to the base set so EVERY owl can mint a new
# tool mid-turn; the cap is bumped by 1 in lockstep so base growth does NOT shrink
# the discretionary per-turn headroom.
# P1 adds memory (always-on agentic recall/preserve) to the base set so EVERY owl
# can recall + durably preserve mid-turn regardless of its capability_profile; the
# cap is bumped by 1 in lockstep so base growth does NOT shrink discretionary
# per-turn headroom.
# Skills-awareness fix adds the two skill DISCOVERY tools (skills_list / skill_view)
# to the base set so a weak/small-window model can always discover and load a skill
# (skill_manage authoring was already base, but discovery was prunable — the bug).
# The cap is bumped by 2 in lockstep so base growth does NOT shrink discretionary
# per-turn headroom.
# P2 adds owl_build (self-extension for owls, sibling to tool_build) to the base set
# so EVERY owl can author a new owl to overcome a capability gap; the cap is bumped
# by 1 in lockstep so base growth does NOT shrink discretionary per-turn headroom.
_DEFAULT_CAP = 34
# Guaranteed base set — read-only/foundation essentials every owl always has.
# Phase B: the self-improvement trio (skill_manage / reflect_now /
# synthesize_skills) joins the base set so EVERY owl can reach self-learning +
# gap-analysis/skill-build mid-turn (not only the nightly scheduler). E8 adds
# send_file so EVERY owl can deliver a workspace file it produced (the agent can
# download but, without surfacing this, could not send the bytes). The
# consequential ones (skill_manage, synthesize_skills, send_file) are still
# consent-gated at dispatch — surfacing them does not bypass consent. The cap is
# bumped in lockstep with each base addition so base growth does NOT shrink the
# discretionary per-turn headroom a full owl profile already had.
_DEFAULT_BASE = frozenset({
    "read_file", "write_file", "shell", "web_fetch",
    "skill_manage", "reflect_now", "synthesize_skills",
    # Skill DISCOVERY — read-only; must survive the budget so a weak model can
    # always find (skills_list) and load (skill_view) an installed skill.
    "skills_list", "skill_view",
    "send_file",
    # H4 — tool_build: every owl can author a new tool to overcome a capability
    # gap (consequential → still consent-gated at dispatch; surfacing ≠ bypass).
    "tool_build",
    # P1 — memory: always-on agentic recall/preserve so every owl can recall what
    # it knows and durably preserve on request, independent of its profile.
    "memory",
    # P2 — owl_build: mirrors tool_build 1:1 — self-extension's owl-equivalent
    # sibling. Consequential → still consent-gated at dispatch; surfacing ≠ bypass.
    # Fixes the "create an agent named Brain" incident (owl_build was evictable).
    "owl_build",
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
        restrict_to: frozenset[str] | None = None,
    ) -> list[Tool]:
        """Return the ordered, capped presented set (deterministic, self-healing)."""
        cfg = self._cfg
        by_name = {t.name: t for t in all_tools}

        # E2-S3 — least-privilege presentation. When a plan exists, present ONLY
        # discovery (always_present) + the planned set ∩ catalog. The broad base
        # set + profile groups are dropped for this turn; always_present stays
        # non-evictable. `is not None`, NOT truthiness — an empty plan yields
        # discovery-only, never a fall back to base+groups.
        if restrict_to is not None:
            always = sorted(n for n in cfg.always_present if n in by_name)
            taken = set(always)
            planned = sorted(n for n in restrict_to if n in by_name and n not in taken)
            ordered = list(always)
            budget = max(cfg.cap - len(ordered), 0)
            ordered.extend(planned[:budget])
            return [by_name[n] for n in ordered]

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

    def rank_candidates(
        self,
        *,
        all_tools: list[Tool],
        profile: list[str] | None,
        pins: list[str] | None,
        hydrated: set[str] | None,
        request_text: str | None,
    ) -> tuple[list[Tool], list[Tool]]:
        """Return (guaranteed, discretionary-ranked) for budgeted presentation.

        Guaranteed = always_present ∪ base (non-evictable). Discretionary =
        pins ∪ hydrated ∪ group-tools; when `profile`/pins/hydrated are all empty,
        ALL non-guaranteed tools are eligible (no full-catalog bypass). Discretionary
        is ordered by lexical relevance to `request_text` (reusing rank_tools);
        unmatched tools follow in a deterministic by-name tail so none are dropped.
        """
        from stackowl.tools.meta.tool_search import CatalogEntry, rank_tools

        cfg = self._cfg
        by_name = {t.name: t for t in all_tools}
        guaranteed_names = sorted(
            n for n in (cfg.always_present | cfg.base_tools) if n in by_name
        )
        guaranteed = [by_name[n] for n in guaranteed_names]
        gset = set(guaranteed_names)

        profile_groups = {g for g in (profile or []) if isinstance(g, str)}
        pin_names = {p for p in (pins or []) if isinstance(p, str)}
        hydrated_names = hydrated or set()

        def _eligible(t: Tool) -> bool:
            if t.name in gset:
                return False
            if not profile_groups and not pin_names and not hydrated_names:
                return True
            return (
                t.name in pin_names
                or t.name in hydrated_names
                or t.manifest.toolset_group in profile_groups
            )

        candidates = [t for t in all_tools if _eligible(t)]
        if request_text:
            entries = [CatalogEntry(t.name, t.description, None) for t in candidates]
            hit_names = [e.name for e in rank_tools(entries, request_text, limit=len(entries))]
            order = {n: i for i, n in enumerate(hit_names)}
            ranked = sorted(candidates, key=lambda t: (order.get(t.name, len(order)), t.name))
        else:
            ranked = sorted(candidates, key=lambda t: t.name)

        log.tool.debug(
            "[presentation] rank_candidates: exit",
            extra={"_fields": {
                "guaranteed": len(guaranteed), "candidates": len(ranked),
                "no_profile": not profile_groups,
            }},
        )
        return guaranteed, ranked
