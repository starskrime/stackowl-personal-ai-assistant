"""HeuristicMatcher — post-tool-call heuristic lookup + event emission.

Per Learning Commit 5 sub-vote: "Post-execution event — emit
``tool.heuristic_match`` on EventBus + log; no execution change". The matcher
runs AFTER the tool returns, looks up active heuristics for the call's
(tool_name, failure_class), and emits an event for downstream subscribers.

Behavior change: zero. The tool ran already; we just publish the signal so
classify/notifications/future hooks can react.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.events.bus import EventBus
    from stackowl.learning.tool_heuristic_store import ToolHeuristicStore
    from stackowl.tools.base import ToolResult


_HEURISTIC_EVENT = "tool.heuristic_match"
_MIN_EVIDENCE_FOR_EVENT = 3


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


async def match_and_emit(
    *,
    tool_name: str,
    tool_result: ToolResult,
    heuristic_store: ToolHeuristicStore | None,
    event_bus: EventBus | None,
) -> None:
    """Look up any matching heuristic for this call; emit an event if found.

    Best-effort: store or bus may be None (tests/dry-run); a failure on either
    side logs a warning but never raises into the tool dispatch path.
    """
    # 1. ENTRY
    log.tool.debug(
        "[heuristic] matcher.match_and_emit: entry",
        extra={"_fields": {
            "tool_name": tool_name, "success": tool_result.success,
            "has_store": heuristic_store is not None,
            "has_bus": event_bus is not None,
        }},
    )
    if heuristic_store is None or event_bus is None:
        return
    # 2. DECISION — failed call → look up by error class
    failure_label = (
        "succeeded" if tool_result.success
        else _extract_error_class(tool_result.error)
    )
    if not failure_label:
        return
    try:
        heuristics = await heuristic_store.find_for_tool(
            tool_name, min_evidence=_MIN_EVIDENCE_FOR_EVENT,
        )
    except Exception as exc:  # B5 — best-effort lookup
        log.tool.warning(
            "[heuristic] matcher.match_and_emit: find_for_tool failed",
            exc_info=exc,
            extra={"_fields": {"tool_name": tool_name}},
        )
        return
    matching = [
        h for h in heuristics
        if h.condition_kind == "failure_class"
        and h.condition_value == failure_label
    ]
    if not matching:
        log.tool.debug(
            "[heuristic] matcher.match_and_emit: no matching heuristic",
            extra={"_fields": {"tool_name": tool_name, "failure": failure_label}},
        )
        return
    # 3. STEP — emit one event per match (EventBus.emit is sync)
    for h in matching:
        try:
            event_bus.emit(_HEURISTIC_EVENT, {
                "tool_name": tool_name,
                "failure_class": failure_label,
                "predicted_outcome": h.predicted_outcome,
                "evidence_count": h.evidence_count,
                "heuristic_id": h.heuristic_id,
                "mean_quality": h.mean_quality,
            })
        except Exception as exc:  # B5 — bus may be down
            log.tool.warning(
                "[heuristic] matcher.match_and_emit: bus.emit failed",
                exc_info=exc,
                extra={"_fields": {"tool_name": tool_name}},
            )
    # 4. EXIT
    log.tool.info(
        "[heuristic] matcher.match_and_emit: emitted",
        extra={"_fields": {
            "tool_name": tool_name, "failure": failure_label,
            "n_matches": len(matching),
        }},
    )
