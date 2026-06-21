"""Reachability probes for consequential default-path subsystems.

Each probe drives the REAL production code/config (not a re-implementation) and
reports whether the subsystem's seam is live on the default owl + default config.
Importing this module self-registers every probe into the census registry.
"""

from __future__ import annotations

from stackowl.health.reachability.census import ProbeResult, reachability_probe


@reachability_probe("skills.discovery_tools_guaranteed")
async def _probe_discovery_tools_guaranteed() -> ProbeResult:
    """skills_list/skill_view must be in the non-evictable tool floor, or a weak
    model's budgeter prunes them and the model can never self-discover skills."""
    from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS, _DEFAULT_BASE

    guaranteed = _DEFAULT_BASE | _DEFAULT_ALWAYS
    missing = {"skills_list", "skill_view"} - guaranteed
    return ProbeResult(
        "skills.discovery_tools_guaranteed",
        reachable=not missing,
        detail="present in guaranteed floor" if not missing else f"MISSING: {sorted(missing)}",
    )


@reachability_probe("skills.global_catalog_default_on")
async def _probe_global_catalog_default_on() -> ProbeResult:
    """The default config must surface a skills catalog, else the default Secretary
    (which owns no skills) never learns skills exist."""
    from stackowl.config.settings import Settings

    on = bool(Settings().skills.global_catalog)
    return ProbeResult(
        "skills.global_catalog_default_on",
        reachable=on,
        detail="default ON" if on else "default OFF — default owl is skill-blind",
    )


@reachability_probe("telegram.table_formatting")
async def _probe_telegram_table_formatting() -> ProbeResult:
    """The Telegram formatter chokepoint must flatten GFM tables (raw pipes break
    in MarkdownV2). Drives the real to_telegram_markdownv2."""
    from stackowl.channels.telegram.formatter import to_telegram_markdownv2

    table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    out = to_telegram_markdownv2(table)
    flattened = "```" in out  # a flattened table arrives as a fenced block
    return ProbeResult(
        "telegram.table_formatting",
        reachable=flattened,
        detail="flatten wired in formatter" if flattened else "raw GFM not flattened",
    )


@reachability_probe("deliver.output_preference_enforcement")
async def _probe_output_preference_enforcement() -> ProbeResult:
    """A stored output_tables=off preference must actually remove tables."""
    from stackowl.channels._format import apply_output_preferences

    table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    enforced = apply_output_preferences(table, {"output_tables": "off"})
    removed = "|" not in enforced
    return ProbeResult(
        "deliver.output_preference_enforcement",
        reachable=removed,
        detail="no_tables enforced" if removed else "preference not enforced",
    )


@reachability_probe("budget.counts_tool_calls")
async def _probe_budget_counts_tool_calls() -> ProbeResult:
    """The step cap must count individual tool dispatches, not just ReAct rounds."""
    from stackowl.authz.bounds import ResourceCaps
    from stackowl.pipeline.budget.governor import BudgetGovernor

    class _Clock:
        def monotonic(self) -> float:
            return 0.0

    gov = BudgetGovernor(
        ResourceCaps(max_steps=2), cost_tracker=None, trace_id="census",
        started_monotonic=0.0, clock=_Clock(),
    )
    # Round 0 but 2 tool calls already → must breach on steps.
    breach = gov.check(0, tool_calls=2)
    tripped = breach is not None and breach.cap == "steps"
    return ProbeResult(
        "budget.counts_tool_calls",
        reachable=tripped,
        detail="tool-call count trips step cap" if tripped else "tool calls not counted",
    )
