"""Role presets for the owl-builder — curated, least-privilege tool allowlists.

Each preset is safe-by-construction: it grants only the tools its role needs.
The builder always adds ROUTER_TOOLS on top (the boundary-router): delegate_task
(so the owl can hand off out-of-scope work) + the discovery meta-tools (so a
present-but-narrow tools allowlist never strands the owl — an empty/over-narrow
frozenset would otherwise deny tool_search itself; see BoundsSpec footgun)."""

from __future__ import annotations

from dataclasses import dataclass

# The boundary-router: every built specialist gets these on top of its preset.
ROUTER_TOOLS: frozenset[str] = frozenset({"delegate_task", "tool_search", "tool_describe"})


@dataclass(frozen=True)
class OwlPreset:
    """A named role template: a curated tool allowlist + presentation metadata."""

    tools: frozenset[str]
    specialty: str
    capability_profile: tuple[str, ...]


PRESETS: dict[str, OwlPreset] = {
    "researcher": OwlPreset(
        tools=frozenset({"read_file", "session_search", "web_search", "web_fetch"}),
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
        tools=frozenset({"read_file", "search_files", "web_search", "web_fetch", "session_search"}),
        specialty="analysis and synthesis",
        capability_profile=("analysis",),
    ),
}
