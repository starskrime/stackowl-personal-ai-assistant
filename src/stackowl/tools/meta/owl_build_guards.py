"""Pure guardrails for owl_build: name quality (STRUCTURAL — no English wordlist),
soft-cap, consent-summary rendering, and the consequential-tool flag set."""
from __future__ import annotations

import re
import unicodedata

from stackowl.owls.registry import OwlRegistry

#: Retained ONLY so an old import keeps resolving; nothing reads it as a limit any
#: more. The live value is settings.owl_limits.max_agent_owls, default 0 =
#: unlimited (Bakir, 2026-08-18).
MAX_AGENT_OWLS = 0


def over_owl_cap(*, current: int, cap: int) -> bool:
    """Is another owl refused? ``cap <= 0`` means UNLIMITED.

    Bakir removed the limit of five, and the measurement supports it: the only real
    cost of another owl is the ground-truth roster in the prompt — 69 chars per owl
    on the live registry, so fifty owls is ~862 tokens, 0.33% of the window. Five
    was a number, not a protection.

    A negative cap is treated as unlimited rather than as "block everything":
    garbage config must not lock the user out of their own platform.
    """
    return cap > 0 and current >= cap


def configured_owl_cap() -> int:
    """The operator's cap, or 0 for unlimited. Never raises — an unreadable config
    must not silently reimpose a limit that was deliberately removed."""
    try:
        from stackowl.pipeline.services import get_services

        cfg = getattr(get_services(), "settings", None)
        return int(cfg.owl_limits.max_agent_owls) if cfg is not None else 0
    except Exception:
        return 0
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
