"""Pure greedy size-fit budgeter for per-turn tool presentation.

Given the model's window and the measured fixed cost (system_prompt + history),
reserve response headroom, then greedily include a non-evictable `guaranteed`
set followed by relevance-ranked `candidates` until the tool-token budget is
spent (or a hard count cap is hit). Deterministic, no I/O. Generic over item
type via `size_of` so it is trivially unit-testable.
"""
from __future__ import annotations

from collections.abc import Callable

# Default max tools presented per turn. The effective cap is configurable via
# OrchestratorSettings.tool_count_cap (threaded through the budget dict as
# "max_tools"); this is the byte-identical fallback when none is supplied.
# Raised 2026-07-22 (owner decision): the registered toolset (~60-78 tools) was
# already exceeding the old cap of 40, silently trimming real tools every turn
# — this is a backstop against a pathological catalog, not a shaping lever, so
# it should sit comfortably above any real toolset, not below it.
HARD_TOOL_COUNT_CAP = 150


def resolve_tool_count_cap(configured: int | None) -> int:
    """Effective tool-count cap: the configured value, or the default fallback.

    ``None`` / non-positive → ``HARD_TOOL_COUNT_CAP`` (byte-identical default).
    """
    if configured is None or configured < 1:
        return HARD_TOOL_COUNT_CAP
    return configured


def tool_budget_tokens(*, window: int, fixed_cost_tokens: int) -> int:
    """Tokens available for tool schemas this turn (may be <= 0 → base only).

    No artificial safety-fraction/response-reserve shrinkage (owner decision
    2026-07-22) — the full window minus the turn's real fixed cost, not a
    pre-emptive smaller number. ``fit_items`` below already tolerates a
    negative budget (discretionary tools simply get none; the guaranteed set
    is never dropped), so this isn't a crash risk, just no longer padded."""
    return window - fixed_cost_tokens


def fit_items[T](
    *,
    guaranteed: list[T],
    candidates: list[T],
    budget: int,
    size_of: Callable[[T], int],
    hard_cap: int = HARD_TOOL_COUNT_CAP,
) -> list[T]:
    """Return guaranteed (always) + as many ranked candidates as fit by size/count.

    Guaranteed items are never dropped (they consume budget first; budget may go
    negative — discretionary then simply gets nothing). Candidates are walked in
    the given (relevance) order; each is added if it fits the remaining budget
    AND the total count is under `hard_cap`.
    """
    out: list[T] = list(guaranteed)
    remaining = budget
    for g in guaranteed:
        remaining -= size_of(g)
    for c in candidates:
        if len(out) >= hard_cap:
            break
        cost = size_of(c)
        if cost <= remaining:
            out.append(c)
            remaining -= cost
    return out
