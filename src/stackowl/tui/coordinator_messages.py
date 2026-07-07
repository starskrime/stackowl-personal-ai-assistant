"""Pure helpers — convert EventBus payloads into Textual messages.

Split out of :mod:`stackowl.tui.coordinator` to keep that module under the
300-line boundary (B2).  All builders are pure functions: payload dict in,
:class:`Message` out (or ``None`` for unknown events).  No I/O, no logging
of payloads (logged by the caller).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stackowl.commands.response import Action
from stackowl.tui.messages import (
    BudgetAlertMessage,
    ComposeAreaStateMessage,
    DegradedProviderMessage,
    EvolutionBadgeMessage,
    FactCitation,
    JobPausedMessage,
    MemoryFactMessage,
    ParliamentClosedMessage,
    ParliamentRoundMessage,
    ParliamentRoundStartedMessage,
    ParliamentStartedMessage,
    PipelineStepMessage,
    ResponseChunkMessage,
    SynthesisArrivedMessage,
    ToastRequestMessage,
)

if TYPE_CHECKING:
    from textual.message import Message


def coerce_citations(raw: Any) -> tuple[FactCitation, ...]:
    """Best-effort conversion of an iterable of dict/FactCitation values."""
    if not raw:
        return ()
    out: list[FactCitation] = []
    for item in raw:
        if isinstance(item, FactCitation):
            out.append(item)
            continue
        if isinstance(item, dict):
            out.append(
                FactCitation(
                    fact_id=str(item.get("fact_id", "")),
                    snippet=str(item.get("snippet", "")),
                    index=int(item.get("index", 0)),
                )
            )
    return tuple(out)


def coerce_actions(raw: Any) -> tuple[Action, ...]:
    """Best-effort conversion of an iterable of dict/Action values.

    Mirrors :func:`coerce_citations` — the EventBus is in-process, so the
    payload usually carries real ``Action`` instances straight through, but a
    dict form is still accepted for parity with the citations helper.
    """
    if not raw:
        return ()
    out: list[Action] = []
    for item in raw:
        if isinstance(item, Action):
            out.append(item)
            continue
        if isinstance(item, dict):
            out.append(
                Action(
                    label=str(item.get("label", "")),
                    command=str(item.get("command", "")),
                    destructive=bool(item.get("destructive", False)),
                )
            )
    return tuple(out)


def build_message(event_name: str, payload: dict[str, Any]) -> Message | None:
    """Construct the Textual message for the given event payload."""
    if event_name == "pipeline_step_changed":
        return PipelineStepMessage(
            step_name=str(payload.get("step_name", "")),
            step_index=int(payload.get("step_index", 0)),
            total_steps=int(payload.get("total_steps", 0)),
        )
    if event_name == "provider_degraded":
        return DegradedProviderMessage(
            provider_name=str(payload.get("provider_name", "")),
            tier=str(payload.get("tier", "")),
            reason=str(payload.get("reason", "")),
        )
    if event_name == "budget_80pct_alert":
        return BudgetAlertMessage(
            pct=float(payload.get("pct", 0.0)),
            cost_today=float(payload.get("cost_today", 0.0)),
        )
    if event_name == "job_paused":
        return JobPausedMessage(
            job_id=str(payload.get("job_id", "")),
            handler=str(payload.get("handler", payload.get("handler_name", ""))),
            last_error=str(payload.get("last_error", "")),
        )
    if event_name == "parliament_started":
        owl_names = payload.get("owl_names", ())
        return ParliamentStartedMessage(
            session_id=str(payload.get("session_id", "")),
            owl_names=tuple(str(n) for n in owl_names) if owl_names else (),
            trigger=str(payload.get("trigger", "explicit")),
        )
    if event_name == "parliament_round_started":
        return ParliamentRoundStartedMessage(
            session_id=str(payload.get("session_id", "")),
            round_number=int(payload.get("round_number", 1)),
        )
    if event_name == "parliament_round_complete":
        responses = payload.get("owl_responses", {})
        return ParliamentRoundMessage(
            session_id=str(payload.get("session_id", "")),
            round_number=int(payload.get("round_number", 0)),
            owl_responses=dict(responses) if isinstance(responses, dict) else {},
        )
    if event_name == "synthesis_arrived":
        disagreements = payload.get("disagreements", ())
        return SynthesisArrivedMessage(
            session_id=str(payload.get("session_id", "")),
            consensus=str(payload.get("consensus", "")),
            recommendation=str(payload.get("recommendation", "")),
            confidence=float(payload.get("confidence", 0.0)),
            disagreements=tuple(str(d) for d in disagreements)
            if disagreements
            else (),
        )
    if event_name == "parliament_session_closed":
        return ParliamentClosedMessage(
            session_id=str(payload.get("session_id", "")),
        )
    if event_name == "memory_fact_updated":
        return MemoryFactMessage(
            fact_id=str(payload.get("fact_id", "")),
            content_preview=str(payload.get("content_preview", "")),
        )
    if event_name == "evolution_batch_complete":
        traits = payload.get("changed_traits", {})
        return EvolutionBadgeMessage(
            owl_name=str(payload.get("owl_name", "")),
            changed_traits=dict(traits) if isinstance(traits, dict) else {},
        )
    if event_name == "response_chunk":
        return ResponseChunkMessage(
            text=str(payload.get("text", "")),
            owl_name=str(payload.get("owl_name", "")),
            citations=coerce_citations(payload.get("citations", ())),
            is_pushback=bool(payload.get("is_pushback", False)),
            is_synthesis=bool(payload.get("is_synthesis", False)),
            chunk_index=int(payload.get("chunk_index", 0)),
            trace_id=str(payload.get("trace_id", "")),
            is_final=bool(payload.get("is_final", False)),
            actions=coerce_actions(payload.get("actions", ())),
        )
    if event_name == "mcp_spectator_active":
        return ComposeAreaStateMessage(state="mcp-disabled")
    if event_name == "mcp_spectator_disconnected":
        return ComposeAreaStateMessage(state="idle")
    if event_name == "toast_request":
        return ToastRequestMessage(
            message=str(payload.get("message", "")),
            urgency=str(payload.get("urgency", "normal")),
        )
    return None
