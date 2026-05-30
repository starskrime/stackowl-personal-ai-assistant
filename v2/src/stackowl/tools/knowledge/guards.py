"""Shared guards for the self-mutation knowledge tools (``memory`` / ``skill_manage``).

This module is the security substrate that the E4 self-mutation tools consume.
It does NOT implement the tools — only the small, pure decision helpers they
call before performing any write.

Two concerns live here:

* :data:`AGENT_SELF_SOURCE_TYPE` — the canonical ``source_type`` value tool-authored
  facts/skills are tagged with, so self-authored content is distinguishable from
  human-authored (``manual``) content for future recall down-ranking and
  privileged-context exclusion (E4 design change #3).
* :func:`deny_if_non_interactive` — the non-interactive default-deny chokepoint
  (E4 design change #4). Self-mutation in an unattended context (cron
  ``interactive=False``) is the worst case for a poisoned skill/fact, so the
  tools must default-deny self-edits when no human is present.
"""

from __future__ import annotations

from dataclasses import dataclass

from stackowl.infra.observability import log

# Canonical source_type value for content authored by the agent's own
# self-mutation tool path (vs. "manual" = human-authored via slash command).
# Down-ranked at recall and excluded from privileged contexts by later stories.
# Kept in lock-step with the StagedFact.source_type Literal and the
# staged_facts CHECK constraint (migration 0036).
AGENT_SELF_SOURCE_TYPE = "agent_self"


@dataclass(frozen=True)
class GuardDecision:
    """Outcome of a guard check.

    ``allowed`` is the only field a caller must branch on. ``reason`` is a
    short, user-surfaceable explanation that the tool can fold into its
    structured (failed) ToolResult. Guards never raise — a denied write is a
    normal, expected outcome, not an error condition.
    """

    allowed: bool
    reason: str = ""


def deny_if_non_interactive(
    *,
    interactive: bool | None,
    operation: str,
) -> GuardDecision:
    """Default-deny self-mutation writes when not in an interactive context.

    The self-mutation tools (``memory`` add/forget, ``skill_manage`` writes)
    must NOT run unattended: a poisoned skill or fabricated "user preference"
    written during a cron run re-injects as trusted first-party context on the
    next turn with no human ever in the loop (E4 Security persona).

    Policy (conservative, fail-closed):

    * ``interactive is True``  → allow (a human is present and will see the diff).
    * ``interactive is False`` → deny (explicit unattended context, e.g. cron).
    * ``interactive is None``  → deny (UNKNOWN context — we cannot prove a human
      is present, so we fail closed rather than assume interactivity).

    Returns a :class:`GuardDecision`; never raises.

    LIMITATION (review-worthy): StackOwl has no clean per-call ``interactive``
    signal today. ``PipelineState`` carries only ``channel`` (a routing label,
    not an attended/unattended flag), and the scheduler's
    ``goal_execution`` handler currently builds its ``PipelineState`` with
    ``channel="cli"`` (see src/stackowl/scheduler/handlers/goal_execution.py),
    so channel cannot be trusted to distinguish cron from an attended CLI run.
    This helper therefore takes ``interactive`` as an explicit parameter that
    the calling tool must thread from its execution context. Wiring the cron /
    scheduler entrypoints to pass ``interactive=False`` is a tool-layer
    follow-up (the tools, not this substrate). Until that wiring lands, callers
    that cannot determine interactivity should pass ``None`` and inherit the
    fail-closed deny.
    """
    # 1. ENTRY
    log.tool.debug(
        "[knowledge] deny_if_non_interactive: entry",
        extra={"_fields": {"operation": operation, "interactive": interactive}},
    )
    if interactive is True:
        # 4. EXIT — allowed
        log.tool.debug(
            "[knowledge] deny_if_non_interactive: allow (interactive)",
            extra={"_fields": {"operation": operation}},
        )
        return GuardDecision(allowed=True)

    # 2. DECISION — fail closed for both False and None
    reason = (
        f"'{operation}' is a self-mutation write and is blocked in a "
        "non-interactive context (no human present to review the change). "
        "Run this from an interactive session."
    )
    if interactive is None:
        reason = (
            f"'{operation}' is a self-mutation write and was blocked because "
            "the interactivity of this context could not be confirmed "
            "(failing closed). Run this from an interactive session."
        )
    # 4. EXIT — denied
    log.tool.warning(
        "[knowledge] deny_if_non_interactive: deny",
        extra={"_fields": {"operation": operation, "interactive": interactive}},
    )
    return GuardDecision(allowed=False, reason=reason)
