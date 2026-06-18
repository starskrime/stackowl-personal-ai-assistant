"""Pure guardrails for owl_build: name quality (STRUCTURAL — no English wordlist),
soft-cap, consent-summary rendering, and the consequential-tool flag set."""
from __future__ import annotations

import re
import unicodedata

from stackowl.owls.registry import OwlRegistry

MAX_AGENT_OWLS = 5
# Tools whose presence is a real privilege — flagged with ⚠ in the consent prompt so
# the human (the real clamp) sees them. web_fetch is read-severity (SSRF-guarded) so
# it is NOT flagged; network egress becomes its own bounds axis in Epic 3.
_CONSEQUENTIAL_TOOL_NAMES = frozenset(
    {"shell", "execute_code", "write_file", "process", "sessions_spawn"}
)
_TRAILING_DIGITS = re.compile(r"\d+$")


def count_agent_owls(registry: OwlRegistry) -> int:
    """Number of agent-minted owls currently registered (soft-cap input)."""
    return sum(1 for m in registry.all() if m.origin == "agent")


def _grapheme_len(s: str) -> int:
    """Approximate grapheme count: drop combining marks so accented letters count once."""
    return len([c for c in s if not unicodedata.combining(c)])


def name_quality_error(name: str, registry: OwlRegistry) -> str | None:
    """Reject low-information / near-duplicate names. Structural only. Returns error or None."""
    n = name.strip().lower()
    if _grapheme_len(n) < 2:
        return "owl name is too short to be a meaningful identity."
    if n.isdigit():
        return "owl name must not be purely numeric."
    existing = {m.name.lower() for m in registry.all()}
    if n in existing:
        return f"an owl named '{name}' already exists."
    stem = _TRAILING_DIGITS.sub("", n)
    if stem and stem != n and stem in existing:
        return (
            f"'{name}' is a near-duplicate of existing owl '{stem}' — delegate to it instead."
        )
    return None


def consent_summary(
    *,
    name: str,
    role: str,
    resolved_tools: frozenset[str],
    dropped: frozenset[str],
    roster: tuple[str, ...],
    why: str,
) -> str:
    """Render the human-facing consent prompt. The human is the real clamp — show everything."""

    def fmt(t: str) -> str:
        return f"⚠ {t}" if t in _CONSEQUENTIAL_TOOL_NAMES else t

    tools_line = ", ".join(fmt(t) for t in sorted(resolved_tools)) or "(none)"
    lines = [
        f"Create owl '{name}' — {role}",
        f"Tools (after clamp): {tools_line}",
    ]
    if dropped:
        lines.append(f"Dropped (above your authority): {', '.join(sorted(dropped))}")
    if roster:
        lines.append(f"You already have {len(roster)} owl(s): {', '.join(roster)}")
    lines.append(f"Model's stated reason: {why}")
    lines.append(
        "⚠ flags consequential tools (shell/exec/write/process). Approve only if intended."
    )
    return "\n".join(lines)
