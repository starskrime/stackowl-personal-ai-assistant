"""HeuristicMatcher — honest, no-IO post-tool-call outcome log.

History: an earlier ``match_and_emit`` did a per-call heuristic DB lookup and
emitted a ``tool.heuristic_match`` EventBus event after every tool call. That
event had ZERO production subscribers (F034/F038/F049) — pure hot-path latency
on the dispatch path. It has been removed; the live path calls
:func:`match_and_log`, which just logs the tool-call outcome (no DB, no bus,
never raises).

Re-introducing a learned-hint consumer is a separate future story with its own
brainstorm + journey: it must NOT steer a weak model on low evidence (the
amplification the reliability arc fought). Note (F049/F046): the synthesizer's
``mean_quality`` is LIVE-consumed; the heuristic-store ``mean_quality`` is mined
by the outcome miner, rendered via ``heuristic_summary``, AND (F046) now feeds a
live ranking DECISION — it scales the UCB exploration nudge in
:func:`stackowl.learning.heuristic_ranking.rank_lessons` so promising
under-observed heuristics surface over mediocre ones. It is therefore no longer a
dead signal.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.tools.base import ToolResult


_ERROR_CLASS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")


def _extract_error_class(error: str | None) -> str:
    """Pull the exception class name out of a tool error string.

    Tool errors land as "ExceptionClass: message" — same convention used by
    classify_failure in outcome_store. Falls back to "tool_error" when the
    format is opaque.
    """
    if not error:
        return ""
    match = _ERROR_CLASS_RE.match(error.strip())
    return match.group(1) if match else "tool_error"


def match_and_log(*, tool_name: str, tool_result: ToolResult) -> None:
    """Honest, no-IO demote of the dead ``tool.heuristic_match`` emit (F038).

    The per-call ``match_and_emit`` did a DB ``find_for_tool`` lookup and emitted an
    EventBus event that had ZERO production subscribers — pure hot-path latency.
    This replacement just logs the tool-call outcome at info level: no DB, no bus,
    never raises. Re-introducing a learned-hint consumer is a separate future story
    with its own brainstorm + journey (it must not steer a weak model on low
    evidence — the amplification the reliability arc fought)."""
    failure_label = "succeeded" if tool_result.success else _extract_error_class(tool_result.error)
    log.tool.info(
        "[heuristic] tool outcome",
        extra={"_fields": {"tool_name": tool_name, "outcome": failure_label or "tool_error"}},
    )
