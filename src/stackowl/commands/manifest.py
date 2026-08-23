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
    # `new` starts a fresh conversation (commands/new_conversation.py, registered
    # at assembly.py:334). It was live but UNDECLARED — drift in the opposite
    # direction from "staged", and just as invisible while the guard was red.
    "new",
    # ── DI commands currently live (Pattern B, Epic A wired) ───────────────
    # /learn — D09.5. Contributes a turn PROMPT rather than a reply.
    "learn",
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
    # "staged" was here. The command was DELETED in D08.1 when the fact-staging
    # queue it reviewed stopped having anything to review — committed_facts has
    # held 0 rows since migration 0112. The entry outlived the command, so this
    # register promised a command that could never be dispatched, and the
    # reachability guards that exist to catch exactly that were themselves red.
    "webhook",
    "connect",
    "disconnect",
    "plugins",
    "onboarding",
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
