"""Role presets for the owl-builder — curated, least-privilege tool allowlists.

Each preset is safe-by-construction: it grants only the tools its role needs.
The builder always adds ROUTER_TOOLS on top (the boundary-router): the APPEAL path
(owl_build/owls_list, so a ceiling can always be questioned) + the discovery
meta-tools (so a present-but-narrow tools allowlist never strands the owl — an
empty/over-narrow frozenset would otherwise deny tool_search itself; see BoundsSpec
footgun).

`delegate_task` was here until 2026-08-23 "so the owl can hand off out-of-scope
work", and ESC-34 measured that this never actually worked: the bounds gate granted
it so a blocked owl could route around a limit, and the task envelope then REFUSED
it as off-plan (8c403494 — "the task envelope is a real boundary"). Two gates
behaving exactly as designed, with contradictory designs. Observed live in
syshealth's first scheduled run: send_message refused by bounds, then tool_search
across a 78-tool catalog looking for a way through, then delegate_task refused
off-plan. Bakir's call was to keep the boundary and drop the vector: an owl should
ASK for the capability via owl_build, not borrow someone else's."""

from __future__ import annotations

from dataclasses import dataclass

# The boundary-router: every built specialist gets these on top of its preset.
#
# `owl_build` + `owls_list` joined 2026-08-21, and the reason is narrow. `owl_build
# action='grant'` is the ONLY sanctioned path that widens a ceiling (389e3902) — and
# it was NOT in a specialist's ceiling, so the tool by which an owl asks for authority
# was itself gated by the authority it lacked. Measured over three days: mailbutler
# was refused `owl_build` SIX times; the request could not even be made.
#
# THE CIRCULARITY IS THE DEFECT, not the narrowness. A narrow ceiling is a legitimate
# choice and this does not widen one. A ceiling that cannot be APPEALED is never
# legitimate, because then the operator's answer is unreachable rather than merely
# unsought.
#
# AND IT CONFERS NO POWER TO ACT. `grant` is gated on the `authority_widening` consent
# category, which is always-ask and stays always-ask: this lets an owl RAISE the
# question, never answer it. `owls_list` rides along at far lower stakes — an owl told
# to delegate cannot name a target it may not enumerate, and it was refused 11 times
# across two owls in the same window.
# ESC-34, Bakir 2026-08-23 — `delegate_task` REMOVED. Note this is applied at BUILD
# time, so it is NOT retroactive: the six already-bounded owls keep it in their
# stored manifests and are unaffected. It changes what NEW owls are granted.
ROUTER_TOOLS: frozenset[str] = frozenset(
    {"tool_search", "tool_describe", "owl_build", "owls_list"}
)

#: THE TOOLS BY WHICH AN AGENT ASKS FOR HELP. These must survive EVERY gate that
#: can remove a tool from a turn, and on 2026-08-22 they survived exactly one.
#:
#: Four independent gates can take a tool away: the owl's effective bounds, the
#: retry's `banned_capabilities`, the task envelope's plan, and the budget/count
#: eviction. `owl_build` was protected at the FIRST only. So the loop would tell a
#: blocked agent, in these exact words, "ask the user to grant it — owl_build
#: action='grant'" and then the retry would ban `owl_build`, because a previous
#: attempt at it had failed (it always failed: `_grant` called register() on an owl
#: that already exists). The platform banned the one tool its own healing depends
#: on, for failing at a job it was structurally incapable of doing.
#:
#: MEASURED 2026-08-22 02:20:25, minutes after a grant finally succeeded:
#: banned=["delegate_task","memory","owl_build"] — BOTH remedies the loop names in
#: its own error text, banned together.
#:
#: `delegate_task` is deliberately NOT here: it is a routing tool with its own
#: fork-bomb rule (a delegated child may not itself delegate), and that rule must
#: keep winning. `tool_search`/`tool_describe` are already non-evictable via
#: `_DEFAULT_ALWAYS`. What was missing is the ASK.
APPEAL_TOOLS: frozenset[str] = frozenset({"owl_build", "owls_list"})


@dataclass(frozen=True)
class OwlPreset:
    """A named role template: a curated tool allowlist + presentation metadata."""

    tools: frozenset[str]
    specialty: str
    capability_profile: tuple[str, ...]


PRESETS: dict[str, OwlPreset] = {
    "researcher": OwlPreset(
        tools=frozenset({"read_file", "memory", "web_search", "web_fetch"}),
        specialty="research and information gathering",
        capability_profile=("research",),
    ),
    "coder": OwlPreset(
        tools=frozenset({"read_file", "write_file", "edit", "search_files", "execute_code", "shell"}),
        specialty="reading, writing and running code",
        capability_profile=("coding",),
    ),
    "writer": OwlPreset(
        tools=frozenset({"read_file", "write_file", "web_fetch"}),
        specialty="drafting and editing written content",
        capability_profile=("writing",),
    ),
    "analyst": OwlPreset(
        tools=frozenset({"read_file", "search_files", "web_search", "web_fetch", "memory"}),
        specialty="analysis and synthesis",
        capability_profile=("analysis",),
    ),
}


#: The platform's ROOT ADMINISTRATOR.
#:
#: BAKIR, 2026-08-22: "Secretary should have access to everything. She is root
#: administrator of platform."
#:
#: This is an authority DECISION by the platform's owner, recorded here as one
#: constant rather than as a string literal repeated at each seam — `_SECRETARY_NAME`
#: was already defined twice (registry.py, dispatch.py), which is exactly how a rule
#: ends up enforced in some places and not others.
#:
#: WHAT IT CHANGES, measured from the failures that prompted it. On 2026-08-22
#: `secretary` was refused `cronjob` and `session_search` by the task envelope, and
#: refused editing `syshealth` with "created by another owl — you may only modify
#: owls you created". Her `bounds` and `creation_ceiling` are BOTH None, so she was
#: already unbounded on tools; what stopped her were the two guards that do not
#: consult bounds at all.
#:
#: WHAT IT DOES NOT CHANGE. The secretary still cannot be modified or retired
#: THROUGH `owl_build` — that is a registry-level mandatory invariant protecting the
#: platform's own entry point, not an authority limit, and root status is a reason
#: to trust her with other owls rather than a reason to let her delete herself.
ROOT_OWL = "secretary"


def is_root_owl(name: str | None) -> bool:
    """True when ``name`` is the platform's root administrator.

    Case- and whitespace-insensitive: this is compared against values that arrive
    from a manifest, a trace context and an LLM-supplied spec, and a rule that
    silently fails on "Secretary" is worse than no rule.
    """
    return bool(name) and str(name).strip().casefold() == ROOT_OWL
