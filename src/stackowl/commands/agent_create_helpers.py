"""Helpers for :class:`AgentCreateCommand` (Story 7.2).

Kept separate to honour B2 (300-line cap) and to allow unit tests to
exercise the LLM-response parser without dragging the whole command in.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from stackowl.exceptions import CommandParseError
from stackowl.infra.observability import log

_VALID_HANDLERS = frozenset({"goal_execution", "morning_brief", "check_in"})

_INTENT_ADAPTER: TypeAdapter[dict[str, Any]] = TypeAdapter(dict[str, Any])


def strip_code_fences(text: str) -> str:
    """Strip ``` fences from an LLM JSON response if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
    if stripped.endswith("```"):
        stripped = stripped[: -len("```")]
    return stripped.strip()


def parse_intent_response(raw: str) -> dict[str, Any]:
    """Parse the LLM JSON response and validate the schema.

    Raises :class:`CommandParseError` (with a useful message) on any
    structural problem so the command surface can report a single error
    type back to the caller.
    """
    cleaned = strip_code_fences(raw.strip())
    try:
        parsed = _INTENT_ADAPTER.validate_json(cleaned)
    except ValidationError as exc:  # B5
        log.scheduler.warning(
            "[commands] agent.parse_intent_response: validation failed",
            exc_info=exc,
            extra={"_fields": {"raw_len": len(raw)}},
        )
        raise CommandParseError("agent", f"LLM JSON validation failed: {exc}") from exc

    handler = parsed.get("handler_name")
    if handler not in _VALID_HANDLERS:
        log.scheduler.warning(
            "[commands] agent.parse_intent_response: unknown handler",
            extra={"_fields": {"handler": handler}},
        )
        raise CommandParseError(
            "agent",
            f"unknown handler '{handler}' — must be one of {sorted(_VALID_HANDLERS)}",
        )
    schedule = parsed.get("schedule")
    if not isinstance(schedule, str) or not schedule.strip():
        log.scheduler.warning(
            "[commands] agent.parse_intent_response: missing schedule",
            extra={"_fields": {"schedule_type": type(schedule).__name__}},
        )
        raise CommandParseError("agent", "LLM response missing valid 'schedule'")
    return parsed


def format_proposal(proposal: dict[str, Any]) -> str:
    """Render a human-readable summary of a pending agent proposal."""
    params = proposal.get("params") or {}
    goal = params.get("goal", "(no goal field)") if isinstance(params, dict) else "(invalid params payload)"
    return (
        "Proposed agent:\n"
        f"  Handler: {proposal.get('handler_name')}\n"
        f"  Schedule: {proposal.get('schedule')}\n"
        f"  Goal: {goal}\n"
        f"  Channel: {proposal.get('primary_channel') or '(default)'}\n\n"
        "Type /agent confirm to create, or /agent cancel to abort."
    )
