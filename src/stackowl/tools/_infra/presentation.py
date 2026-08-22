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
    from collections.abc import Mapping

from stackowl.owls.tool_presets import APPEAL_TOOLS as _APPEAL_TOOLS
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
# owls_list adds the read-only survey seam owl_build lacks (create/edit/retire
# only, no query action) — a "check what owls exist" request had nowhere to go
# except a failing owl_build call missing required fields. Cap bumped by 1.
# Story 3.1 adds evolve_now — the self-improvement trio's DNA-evolution
# sibling to reflect_now/synthesize_skills (FR-12: an owl must be able to
# trigger its own DNA evolution mid-turn, not only via the nightly batch).
# Without base membership it would only be reachable through tool_search's
# fuzzy ranking — the exact "registered but not reachable" failure mode this
# codebase has hit repeatedly for other self-extension tools. Cap bumped by 1.
_DEFAULT_CAP = 36
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
    # Story 3.1 — evolve_now: on-demand DNA evolution mid-turn, the
    # self-improvement trio's third sibling (see cap-bump comment above).
    "evolve_now",
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
    # owls_list — read-only survey of already-configured owls, mirrors
    # skills_list. owl_build has no query action; without this, "check what
    # owls exist" had nowhere to go except a failing owl_build call.
    "owls_list",
    # cronjob — mirrors the owl_build fix 1:1: as a discretionary/ranked
    # candidate, lexical relevance to the request text decided whether a
    # reminder ask ("ping me in 5 min" scored well; "remind me ... at 15:00"
    # didn't) ever got cronjob a slot. A one-shot/recurring reminder is as
    # fundamental as self-extension — guarantee it instead of gambling on
    # wording. Consequential → still consent-gated at dispatch.
    "cronjob",
})
#: GATE 4 of 4. Non-evictable under the token budget and the tool-count cap.
#:
#: `APPEAL_TOOLS` JOINED ON 2026-08-22, and the reason is the same one that
#: applied at the other three gates: an agent must always be able to ASK for what
#: it lacks. Discovery (tool_search/tool_describe) was already protected here —
#: finding a tool was guaranteed while REQUESTING one was evictable, so under a
#: full roster the agent could discover exactly what it needed and lose the means
#: to ask for it.
#:
#: Measured the same night: `owl_build` was removable by the owl's bounds, by the
#: retry's ban list, by a task envelope that did not foresee it, and by this
#: eviction — four independent gates, one of which was protected. The others were
#: fixed together rather than one at a time, because fixing one and calling it
#: done is exactly how this recurred.
_DEFAULT_ALWAYS = frozenset({"tool_search", "tool_describe"}) | _APPEAL_TOOLS


def _capability_ok(tool: Tool) -> bool:
    """Whether ``tool``'s required subsystem can run (D05.3). Fails OPEN.

    Callers must apply this to the DISCRETIONARY set only — the guaranteed base
    set is never availability-gated (invariant I2), so that a probe bug cannot
    leave an owl with an empty toolbox.
    """
    from stackowl.infra.capabilities import resolve

    required = getattr(tool.manifest, "requires_capability", None)
    if not required:
        return True
    return resolve(required).ok


@dataclass(frozen=True)
class PresentationConfig:
    """Tunable presentation policy (cap + the always/base membership sets)."""

    cap: int = _DEFAULT_CAP
    base_tools: frozenset[str] = _DEFAULT_BASE
    always_present: frozenset[str] = _DEFAULT_ALWAYS


def _declared_priority(tool: Tool) -> int:
    """Declared cold-start ordering weight; an unreadable manifest means 0.

    ONE reader for both presentation paths — `select` (count-capped) and
    `rank_candidates` (token-budgeted). They cut on different constraints and both
    used to cut alphabetically; a second copy of this rule is how one of them would
    silently keep doing so.
    """
    try:
        return int(getattr(tool.manifest, "presentation_priority", 0) or 0)
    except Exception:  # pragma: no cover — a broken manifest must not stop presentation
        return 0


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
        #
        # D05.3 — every tier below is filtered by _capability_ok. All three are
        # DISCRETIONARY; `guaranteed` above is built without the filter, which is
        # invariant I2 (a probe bug must never empty an owl's toolbox).
        guaranteed_set = set(guaranteed)
        pins_tier = sorted(
            n for n in pin_names
            if n in by_name and n not in guaranteed_set and _capability_ok(by_name[n])
        )
        taken = guaranteed_set | set(pins_tier)
        hydrated_tier = sorted(
            n for n in hydrated_names
            if n in by_name and n not in taken and _capability_ok(by_name[n])
        )
        taken |= set(hydrated_tier)
        # ESC-9 — ordered by DECLARED priority, then name. This tier is what the
        # cap truncates, and sorting it by name alone meant the cut fell on the
        # alphabet: a browser-profiled owl lost snapshot, type, wait_for, upload,
        # vision and the tab tools purely for sorting late, leaving it able to
        # click but not to SEE the page. Priority is a property of the tool, never
        # of the query, so the presented array stays stable turn to turn (D05.2).
        group_tier = sorted(
            (
                n for n, t in by_name.items()
                if t.manifest.toolset_group in profile_groups
                and n not in taken
                and _capability_ok(t)
            ),
            key=lambda n: (-_declared_priority(by_name[n]), n),
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
        # 4. EXIT
        chosen = set(ordered_names)
        dropped_names = [
            n for tier in (pins_tier, hydrated_tier, group_tier)
            for n in tier if n not in chosen
        ]
        if dropped_names:
            # ESC-9 — INFO and NAMED. This was a DEBUG count, so an operator asking
            # "why can't my browser owl type?" had nothing to read: production runs
            # at INFO, and a number does not say which tool went.
            log.tool.info(
                "[presentation] select: eligible tools NOT presented — the owl's "
                "tool-count cap could not fit them",
                extra={"_fields": {
                    "dropped": dropped_names[:20],
                    "dropped_count": len(dropped_names),
                    "presented": len(selected),
                    "cap": cfg.cap,
                }},
            )
        log.tool.debug(
            "[presentation] select: exit",
            extra={"_fields": {
                "presented": len(selected), "guaranteed": len(guaranteed),
                "dropped": len(dropped_names),
            }},
        )
        return selected

    @staticmethod
    def _declared_priority_of(tool: Tool) -> int:
        return _declared_priority(tool)

    def rank_candidates(
        self,
        *,
        all_tools: list[Tool],
        profile: list[str] | None,
        pins: list[str] | None,
        hydrated: set[str] | None,
        usage_scores: Mapping[str, float] | None = None,
    ) -> tuple[list[Tool], list[Tool]]:
        """Return (guaranteed, discretionary-ranked) for budgeted presentation.

        Guaranteed = always_present ∪ base (non-evictable). Discretionary =
        pins ∪ hydrated ∪ group-tools; when `profile`/pins/hydrated are all empty,
        ALL non-guaranteed tools are eligible (no full-catalog bypass). Discretionary
        is ordered by MEASURED PER-OWL USAGE (`usage_scores`, highest first);
        unscored tools follow in a deterministic by-name tail so none are dropped.

        D05.2 — this used to rank by lexical relevance to the turn's `request_text`.
        That made the presented array a function of the QUESTION, so it changed
        every turn and defeated the position-0 prompt-cache marker D01.2 places
        (D01.3 measured 15 change events across 5 (lane, owl) pairs). The ordering
        signal is now stable for the life of a session by construction: it comes
        from what this owl has historically used, not from what was just asked.

        `usage_scores` empty or None → pure by-name order, which is what the
        cold-start path wants and is already deterministic. NOT a degraded mode.
        """
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
            # D05.3 — never offer a tool whose subsystem cannot run. Applied to
            # the DISCRETIONARY set only: `gset` (base ∪ always_present) returned
            # False above, so protected tools are never reached by this check.
            # That is invariant I2 — a probe bug must not be able to leave an owl
            # with an empty toolbox — and its stated cost is that with no network
            # web_fetch stays visible and fails when called.
            if not _capability_ok(t):
                return False
            if not profile_groups and not pin_names and not hydrated_names:
                return True
            return (
                t.name in pin_names
                or t.name in hydrated_names
                or t.manifest.toolset_group in profile_groups
            )

        candidates = [t for t in all_tools if _eligible(t)]
        # Highest score first, then by name. The name is ALWAYS part of the key,
        # not just a fallback for unscored tools — two tools on an equal score
        # must not order by list position, or the array would depend on registry
        # iteration order and the stability this exists for would be luck.
        scores = usage_scores or {}

        # ESC-9 — measured usage first (evidence beats a declaration), then the
        # tool's DECLARED priority, then the name. Before the middle term the key
        # collapsed to the alphabet whenever an owl had no usage history, which is
        # how a browser owl lost the tools that let it see and type.
        ranked = sorted(
            candidates,
            key=lambda t: (-scores.get(t.name, 0.0), -_declared_priority(t), t.name),
        )

        log.tool.debug(
            "[presentation] rank_candidates: exit",
            extra={"_fields": {
                "guaranteed": len(guaranteed), "candidates": len(ranked),
                "no_profile": not profile_groups, "scored": len(scores),
            }},
        )
        return guaranteed, ranked
