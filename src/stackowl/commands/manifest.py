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
# Shipped commands — the product's full slash-command surface (Epic B complete).
# ``owls`` and ``agent`` were retired in Task 7 — folded into the unified
# ``owl`` command (see stackowl/commands/owls_command.py:OwlCommand).
# ---------------------------------------------------------------------------
SHIPPED_COMMANDS: frozenset[str] = frozenset({
    # ── Dependency-free module-level commands (Pattern A) ──────────────────
    "help",
    "find",
    "config",
    "cost",
    "tools",
    "provider",
    "tier",
    "browser",
    "explain",
    # ── DI commands currently live (Pattern B, Epic A wired) ───────────────
    "skill",
    "memory",
    "owl",
    "focus",
    "style",
    "preferences",
    "urgent",
    "quiet",
    "notifications",
    # ── DI commands to be wired in Epic B ──────────────────────────────────
    "bye",
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
EXEMPT_COMMANDS: frozenset[str] = frozenset({
    # OwlsCommand.command == "owls" — the class survives, unregistered, as the
    # base class OwlCommand ("owl") inherits its registry-backed handlers from.
    # Not a live command; retired in Task 7.
    "owls",
})
