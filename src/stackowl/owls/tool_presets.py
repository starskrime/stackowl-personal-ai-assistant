"""Role presets for the owl-builder — curated, least-privilege tool allowlists.

Each preset is safe-by-construction: it grants only the tools its role needs.
The builder always adds ROUTER_TOOLS on top (the boundary-router): delegate_task
(so the owl can hand off out-of-scope work) + the discovery meta-tools (so a
present-but-narrow tools allowlist never strands the owl — an empty/over-narrow
frozenset would otherwise deny tool_search itself; see BoundsSpec footgun)."""

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
ROUTER_TOOLS: frozenset[str] = frozenset(
    {"delegate_task", "tool_search", "tool_describe", "owl_build", "owls_list"}
)


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
