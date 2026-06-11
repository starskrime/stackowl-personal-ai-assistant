"""Surface CRITICAL pipeline-step failures to the user (Phase 2 #2).

The pipeline backends self-heal: a step exception is logged at ERROR, appended
to ``state.errors``, and the loop CONTINUES. That is correct for NON-critical
steps (assemble/classify degrade gracefully). But when the CRITICAL,
answer-producing step (``execute``) fails AND no usable response was produced,
the user is otherwise left with silence — no indication anything broke.

This module provides a SHARED helper both the asyncio and langgraph backends
call just before ``deliver``. It detects a critical failure and, if found,
injects a single user-facing apology ResponseChunk so ``deliver`` sends it.

Multilingual constraint (project rule — there is NO i18n system): the apology
is generated in the USER'S language via the provider cascade (a healthy fallback
provider may answer even though the one that failed is OPEN). If that ALSO fails
(total outage), a neutral, language-agnostic last-resort marker is used.
Known limitation: the last-resort line is not localized (no i18n infrastructure).

No-hidden-errors: the failure is now VISIBLE to the user, not just in logs. The
helper itself is self-healing — its own failures fall back to the neutral
message and it NEVER raises into the backend.
"""

from __future__ import annotations

import json

from stackowl.infra.observability import log
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import Message

# The answer-producing step(s). A failure here with no usable response is what
# leaves the user in silence; non-critical steps self-heal and stay silent.
_CRITICAL_STEPS: frozenset[str] = frozenset({"execute"})

# Delegation statuses that mean the parent received NO usable sub-task answer.
# ``ok`` and ``recovered_via_secretary`` indicate the model DID get content.
# ``truncated`` has partial content — treat as an answer; do not surface.
# ``refused`` is a safety rail the model recovers from inline; do not surface.
_DELEGATION_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"timeout", "child_error", "empty", "cycle", "target_not_found"}
)

# Tier to start the apology cascade from. A different/healthy provider in the
# cascade may answer even when the owl's own provider tripped its breaker.
_APOLOGY_TIER = "fast"
# Keep the apology generation tiny — one short sentence is the whole budget.
_APOLOGY_MAX_TOKENS = 60

# Language-agnostic last-resort. Used ONLY when the cascade apology itself fails
# (total provider outage). A warning sign + a short stable marker + the failure
# class for debuggability. Not localized — documented known limitation (no i18n).
_NEUTRAL_PREFIX = "⚠ "  # warning sign


def _has_usable_response(state: PipelineState) -> bool:
    """True when at least one accumulated chunk carries a GENUINE (non-floor) answer.

    The execute site writes a deterministic never-empty FLOOR chunk (``is_floor``)
    as the zero-provider backstop. A response made up ONLY of floor chunks is the
    honest last resort — NOT a real answer — so it must NOT short-circuit the
    critical-failure cascade: a localized LLM apology (better UX) should still get
    the chance to REPLACE it while any provider is alive. A genuine chunk (non-empty
    content, ``is_floor`` False) is a real answer and DOES short-circuit the cascade.
    """
    return any(c.content and not c.is_floor for c in state.responses)


def _has_floor_only(state: PipelineState) -> bool:
    """True when the response is non-empty but consists SOLELY of floor chunks.

    This is the replaceable backstop: the cascade ran because there is no genuine
    answer, yet a deterministic floor is already present. When the cascade produces
    a localized apology we DROP these floor chunks and substitute the apology; when
    the cascade ALSO fails we KEEP them (the floor already supersedes the neutral
    ``⚠ [marker]`` as the better honest fallback).
    """
    floors = [c for c in state.responses if c.content and c.is_floor]
    return bool(floors) and not _has_usable_response(state)


def _critical_failure_classes(state: PipelineState) -> list[str]:
    """Return the failure class for each critical step that recorded an error.

    Errors are stored as ``"<step>: <ExcType>: <msg>"``. We scan for the
    ``"<step>: "`` prefix of any critical step and extract the ``<ExcType>`` for
    a compact, debuggable marker in the neutral fallback.
    """
    classes: list[str] = []
    for err in state.errors:
        for step in _CRITICAL_STEPS:
            prefix = f"{step}: "
            if err.startswith(prefix):
                rest = err[len(prefix):]
                exc_type = rest.split(":", 1)[0].strip() or "error"
                classes.append(exc_type)
    return classes


def _delegation_failed_with_no_answer(state: PipelineState) -> bool:
    """True when a delegate_task call recorded a terminal status AND the parent
    produced no usable answer — the swallowed-delegation failure case.

    Guards:
    * Returns False immediately if the parent has any usable response (the model
      recovered on its own — do NOT inject an apology over a real answer).
    * Scans ``state.tool_calls`` for records whose parsed JSON carries
      ``{"record": {"status": <terminal>}}``; returns True on the first match.
    * JSON parsing is DEFENSIVE — any parse error or unexpected shape is skipped;
      the helper never raises (B5: the safety net must not crash the pipeline).
    """
    if _has_usable_response(state):
        return False
    for tc in state.tool_calls:
        if tc.result is None:
            continue
        try:
            parsed = json.loads(tc.result)
        except (json.JSONDecodeError, ValueError):
            continue
        record = parsed.get("record")
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status in _DELEGATION_TERMINAL_STATUSES:
            return True
    return False


def detect_critical_failure(state: PipelineState) -> bool:
    """True when a CRITICAL step recorded an error AND there is no usable response,
    OR when a delegate_task call swallowed a terminal failure with no parent answer.

    Both conditions for the execute-error path are required: a critical step that
    errored but still produced a partial answer (e.g. token-limit truncation) is
    NOT silence, so we don't inject an apology over a real (if partial) response.
    The delegation-failure predicate applies the same guard (``_has_usable_response``
    is the first check in both helpers).
    """
    if _has_usable_response(state):
        return False
    return bool(_critical_failure_classes(state)) or _delegation_failed_with_no_answer(state)


async def _generate_localized_apology(
    state: PipelineState, services: StepServices,
) -> str | None:
    """Best-effort: a ONE-sentence apology in the user's language via the cascade.

    Returns the apology text, or None if no provider could be reached / it failed.
    Never raises — the caller falls back to the neutral marker on None.
    """
    registry = services.provider_registry
    if registry is None:
        log.engine.debug(
            "[critical_failure] apology: no provider_registry — neutral fallback",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    try:
        provider = registry.get_with_cascade(_APOLOGY_TIER)
    except Exception as exc:  # AllProvidersUnavailableError or any lookup failure
        log.engine.warning(
            "[critical_failure] apology: cascade found no provider — neutral fallback",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    # Minimal prompt; the model must answer in the SAME language as the user.
    system_text = (
        "You write a single short apology sentence in the SAME language as the "
        "user's message. No preamble, no explanation, no quotes — one sentence only."
    )
    user_text = (
        f"The user said: {state.input_text}\n"
        "Reply with ONE short sentence, in the SAME language as the user, "
        "apologizing that their request could not be completed right now."
    )
    try:
        result = await provider.complete(
            [
                Message(role="system", content=system_text),
                Message(role="user", content=user_text),
            ],
            model="",
            max_tokens=_APOLOGY_MAX_TOKENS,
        )
    except Exception as exc:  # provider call itself failed (outage mid-cascade)
        log.engine.warning(
            "[critical_failure] apology: provider.complete failed — neutral fallback",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None

    text = (result.content or "").strip()
    if not text:
        log.engine.warning(
            "[critical_failure] apology: provider returned empty — neutral fallback",
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        return None
    log.engine.info(
        "[critical_failure] apology: localized message generated",
        extra={"_fields": {"trace_id": state.trace_id, "len": len(text)}},
    )
    return text


def _neutral_fallback(state: PipelineState) -> str:
    """Language-agnostic last-resort message (no i18n infra — known limitation)."""
    classes = _critical_failure_classes(state)
    marker = classes[0] if classes else "error"
    return f"{_NEUTRAL_PREFIX}[{marker}]"


async def surface_critical_failure(
    state: PipelineState, services: StepServices,
) -> PipelineState:
    """If a critical step failed with no response, inject a user-facing apology.

    Returns the (possibly evolved) state to deliver. Self-healing: NEVER raises —
    on any internal failure it still returns a state carrying the neutral marker
    so the user is never left in silence. Must run BEFORE ``deliver.run(...)``.
    """
    try:
        if not detect_critical_failure(state):
            return state
        log.engine.warning(
            "[critical_failure] surfacing: critical step failed with no response",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "failure_classes": _critical_failure_classes(state),
                "error_count": len(state.errors),
            }},
        )
        floor_only = _has_floor_only(state)
        text = await _generate_localized_apology(state, services)
        if not text:
            # Cascade failed (no healthy provider). If a deterministic floor is
            # already present, KEEP it — it is the honest zero-provider backstop and
            # already supersedes the neutral ``⚠ [marker]``. Only when there is NO
            # floor (e.g. the swallowed-delegation path) do we emit the neutral
            # last-resort so the user is never left in silence.
            if floor_only:
                log.engine.warning(
                    "[critical_failure] surfacing: cascade down — keeping deterministic floor",
                    extra={"_fields": {"trace_id": state.trace_id}},
                )
                return state
            text = _neutral_fallback(state)
            log.engine.warning(
                "[critical_failure] surfacing: using neutral last-resort (no i18n)",
                extra={"_fields": {"trace_id": state.trace_id}},
            )
        chunk = ResponseChunk(
            content=text,
            is_final=False,
            chunk_index=0,
            trace_id=state.trace_id,
            owl_name=state.owl_name,
        )
        # When the cascade produced a localized apology AND a floor backstop is
        # present, the apology is the preferred layer: DROP the floor chunk(s) and
        # substitute the apology. ``errors`` is never touched here — the responses-only
        # invariant holds, so durable status / A2A / parliament still see a FAILURE.
        if floor_only:
            kept = tuple(c for c in state.responses if not c.is_floor)
            return state.evolve(responses=(*kept, chunk))
        return state.evolve(responses=(*state.responses, chunk))
    except Exception as exc:  # B5 — the surfacing helper must never break the run
        log.engine.error(
            "[critical_failure] surfacing: helper failed — emitting neutral marker",
            exc_info=exc,
            extra={"_fields": {"trace_id": state.trace_id}},
        )
        try:
            chunk = ResponseChunk(
                content=_neutral_fallback(state),
                is_final=False,
                chunk_index=0,
                trace_id=state.trace_id,
                owl_name=state.owl_name,
            )
            return state.evolve(responses=(*state.responses, chunk))
        except Exception:  # truly last resort — return state untouched, log only
            log.engine.error(
                "[critical_failure] surfacing: neutral injection also failed",
                exc_info=True,
                extra={"_fields": {"trace_id": state.trace_id}},
            )
            return state
