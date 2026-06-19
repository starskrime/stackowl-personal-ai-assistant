"""Command manifest — the contract enforced by the reachability guard.

``SHIPPED_COMMANDS`` is the complete set of slash-command strings the product
ships.  The reachability guard (``tests/journeys/commands/test_reachability_guard.py``)
asserts ``set(registry.list()) == SHIPPED_COMMANDS`` — so adding a command
class without updating this set turns the guard RED immediately (xfail
strict=True).

``EXEMPT_COMMANDS`` holds ``.command`` values that exist as SlashCommand
subclasses but are intentionally NOT in SHIPPED_COMMANDS yet (transitional /
to-be-deleted / folded).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shipped commands — all ~29 that the product intends to expose.
# 15 are live today (Epic A); the remaining 14 are wired in Epic B.
# ---------------------------------------------------------------------------
SHIPPED_COMMANDS: frozenset[str] = frozenset({
    # ── Dependency-free module-level commands (Pattern A) ──────────────────
    "help",
    "config",
    "settings",
    "cost",
    "tools",
    "provider",
    "tier",
    "browser",
    # ── DI commands currently live (Pattern B, Epic A wired) ───────────────
    "skill",
    "memory",
    "owls",
    "focus",
    "urgent",
    "quiet",
    "notifications",
    # ── DI commands to be wired in Epic B ──────────────────────────────────
    "agents",
    "agent",
    "reset",
    "permissions",
    "audit",
    "whoami",
    "why",
    "brief",
    "parliament",
    "staged",
    "webhook",
    "connect",
    "disconnect",
    "plugins",
})

# ---------------------------------------------------------------------------
# Exempt — SlashCommand subclasses that exist in stackowl.commands but are
# intentionally NOT shipped (transitional / folded into another command).
# ---------------------------------------------------------------------------
EXEMPT_COMMANDS: frozenset[str] = frozenset()
