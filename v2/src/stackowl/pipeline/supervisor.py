"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.persistence import (
    CAPABILITY_GAP_DIRECTIVE,
    PERSISTENCE_DIRECTIVE,
    is_structural_giveup,
)
from stackowl.setup.localize import localize, localize_format

_ERROR_MAX_LEN = 500

# Escalation waives the per-nudge budget cost but never suspends this absolute ceiling.
# A tool-spamming weak model that makes a new call every round would otherwise nudge forever.
MAX_TURN_NUDGES = 6


def tally_tool_outcomes(all_calls: list[dict[str, object]]) -> tuple[int, int]:
    """Count failed/successful tool calls from the AUTHORITATIVE typed ``failed`` bool.

    NEVER re-scan ``call["result"]`` for ``TOOL_FAILED_MARKER`` — the marker is
    stripped before the result is stored (``anthropic_provider.py:286`` /
    ``openai_provider.py``), so a re-scan is always False and the structural net
    would silently never fire.
    """
    failures = sum(1 for c in all_calls if bool(c.get("failed")))
    successes = sum(1 for c in all_calls if not bool(c.get("failed")))
    log.engine.debug(
        "supervisor.tally",
        extra={"_fields": {"failures": failures, "successes": successes}},
    )
    return failures, successes


def apply_structural_veto(
    *,
    judge_directive: str | None,
    all_calls: list[dict[str, object]],
    draft: str,
    consequential_giveup: bool = False,
) -> str | None:
    """Always-on structural veto over the judge's verdict.

    Precedence (highest → lowest):
    1. Explicit ``judge_directive`` — kept verbatim if set.
    2. Zombie structural signal (``is_structural_giveup``) — no tool succeeded AND
       draft is trivial/refusing.
    3. Consequential-gap signal (``consequential_giveup``) — a write/consequential
       action failed and NONE succeeded AND no substitution bridged the gap,
       regardless of how substantive the draft reads (catches the dressed-up case
       the zombie misses). Computed by the caller via
       :func:`~stackowl.pipeline.giveup_floor.is_consequential_giveup_now`.

    Pure; never raises; defaults preserve the previous two-signal behavior.
    """
    if judge_directive is not None:
        return judge_directive
    failures, successes = tally_tool_outcomes(all_calls)
    if is_structural_giveup(tool_failures=failures, successful_tool_calls=successes, draft=draft):
        log.engine.debug("supervisor.veto: overriding judge DELIVERED on structural give-up")
        return PERSISTENCE_DIRECTIVE
    if consequential_giveup:
        log.engine.info(
            "supervisor.veto: consequential outcome not achieved — capability-gap directive",
        )
        return CAPABILITY_GAP_DIRECTIVE
    return None


def decide_nudge(
    *,
    judge_directive: str | None,
    all_calls: list[dict[str, object]],
    draft: str,
    nudge_budget: int,
    calls_at_last_nudge: int | None,
    consequential_giveup: bool = False,
    nudges_issued: int = 0,
    max_nudges: int = MAX_TURN_NUDGES,
) -> tuple[str | None, int, int | None]:
    """Decide whether to nudge, applying the veto THEN the escalation-reward cap.

    Pure; never raises. Reused by every provider's enforce loop so the self-heal
    budget logic lives in ONE place.

    ``consequential_giveup`` must be pre-computed by the caller via
    :func:`~stackowl.pipeline.giveup_floor.is_consequential_giveup_now`, which
    reads the turn-scoped ledger + recovery context and accounts for substitution
    recovery (so a bridged capability gap does NOT look like a give-up).

    Returns ``(directive_or_None, new_budget, new_calls_at_last_nudge)``:

    1. Run :func:`apply_structural_veto` — keeps an explicit judge directive,
       otherwise OVERRIDES a (possibly hallucinated/erroring) DELIVERED when the
       turn is structurally a give-up. No give-up signal -> ``(None, budget,
       last)`` (budget + marker untouched; no nudge issued).
    2. Budget exhausted (``<= 0``) -> ``(None, budget, last)``: accept the draft;
       the never-empty floor (a later task) is the final backstop.
    3. ESCALATION-REWARD CAP: decrement the budget by default (every nudge
       issued costs budget), EXCEPT when the model escalated since the last
       nudge — i.e. ``calls_at_last_nudge is not None and len(all_calls) >
       calls_at_last_nudge`` (it made a NEW tool call, a real escalation). Then
       the budget is left intact (escalation is rewarded, not penalised). A
       first-ever nudge (``calls_at_last_nudge is None``) and a pure re-refusal
       (no growth) both decrement. The marker always advances to
       ``len(all_calls)``.
    """
    if nudges_issued >= max_nudges:
        log.engine.info(
            "supervisor.decide_nudge: absolute nudge ceiling reached — accepting (floor is the backstop)",
            extra={"_fields": {"nudges_issued": nudges_issued, "max_nudges": max_nudges}},
        )
        return None, nudge_budget, calls_at_last_nudge

    directive = apply_structural_veto(
        judge_directive=judge_directive,
        all_calls=all_calls,
        draft=draft,
        consequential_giveup=consequential_giveup,
    )
    if directive is None:
        return None, nudge_budget, calls_at_last_nudge
    if nudge_budget <= 0:
        log.engine.debug(
            "supervisor.decide_nudge: budget exhausted — accepting (floor is the backstop)"
        )
        return None, nudge_budget, calls_at_last_nudge

    current = len(all_calls)
    escalated = calls_at_last_nudge is not None and current > calls_at_last_nudge
    new_budget = nudge_budget if escalated else nudge_budget - 1
    log.engine.info(
        "supervisor.decide_nudge: nudging",
        extra={
            "_fields": {
                "escalated": escalated,
                "new_budget": new_budget,
                "calls": current,
                "calls_at_last_nudge": calls_at_last_nudge,
            }
        },
    )
    return directive, new_budget, current


def synthesize_floor(
    goal: str | None,
    error: str | None,
    attempts: list[str] | None,
    partial: str | None,
    *,
    failed_capability: str | None = None,
    lang: str = "en",
) -> str:
    """Pure, deterministic never-empty honest floor message — NO model, NO await, NO I/O.

    The TerminalResponseGuarantee core synthesizer. Builds an honest "couldn't
    finish" message from whatever turn data survived, via
    :func:`localize_format`. Guarantees a non-empty string on ANY exit path: on
    any error (including ``None`` inputs causing issues) it returns the static
    localized minimal fallback. NEVER raises, NEVER returns empty.

    ``failed_capability`` — when ``None`` it is derived from ``attempts[0]``;
    :func:`synthesize_from_calls` passes the precise failed tool name.

    This function ONLY produces a string — it never touches ``errors`` or
    pipeline state (the responses-only invariant is enforced at the call sites).
    """
    log.engine.debug(
        "supervisor.synthesize_floor: entry",
        extra={
            "_fields": {
                "has_goal": goal is not None,
                "has_error": error is not None,
                "n_attempts": len(attempts) if attempts else 0,
                "has_partial": bool(partial),
                "failed_capability": failed_capability,
                "lang": lang,
            }
        },
    )
    try:
        attempts_list = list(attempts) if attempts else []
        derived_capability = failed_capability
        if derived_capability is None:
            derived_capability = attempts_list[0] if attempts_list else ""
        # No real capability data (e.g. a bare time/step backstop timeout) → a warm,
        # honest, slot-free message instead of the blank capability template.
        if not derived_capability and not attempts_list and not partial:
            graceful = localize("self_heal_floor_graceful", lang)
            if graceful:
                log.engine.debug(
                    "supervisor.synthesize_floor: graceful (no capability data)",
                    extra={"_fields": {"lang": lang}},
                )
                return graceful
        result = localize_format(
            "self_heal_floor",
            lang,
            goal=goal or "",
            failed_capability=derived_capability or "",
            attempts=", ".join(attempts_list) if attempts_list else "",
            partial=partial or "",
            error=error or "",
        )
        if not result:
            raise ValueError("empty floor result")
        log.engine.debug(
            "supervisor.synthesize_floor: exit",
            extra={"_fields": {"result_len": len(result)}},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.engine.error(
            "supervisor.synthesize_floor: falling back to minimal",
            exc_info=exc,
            extra={"_fields": {"has_goal": goal is not None, "lang": lang}},
        )
        return localize("self_heal_floor_minimal", lang)


def synthesize_from_calls(
    goal: str | None,
    all_calls: list[dict[str, object]],
    partial: str | None,
    *,
    lang: str = "en",
) -> str:
    """Floor entry point for the provider empty-wrap-up path (has ``all_calls``).

    Derives the precise ``failed_capability`` (FIRST call whose ``failed`` bool
    is truthy), the ``attempts`` list and the failing ``error`` from the tool
    records, then delegates to :func:`synthesize_floor`. Pure; never raises.
    """
    log.engine.debug(
        "supervisor.synthesize_from_calls: entry",
        extra={"_fields": {"n_calls": len(all_calls) if all_calls else 0, "lang": lang}},
    )
    try:
        calls = list(all_calls) if all_calls else []
        failed_capability = ""
        error = ""
        for c in calls:
            if bool(c.get("failed")):
                failed_capability = str(c.get("name") or "")
                error = str(c.get("result") or "")[:_ERROR_MAX_LEN]
                break
        attempts = [str(c.get("name") or "") for c in calls]
        return synthesize_floor(
            goal,
            error,
            attempts,
            partial,
            failed_capability=failed_capability,
            lang=lang,
        )
    except Exception as exc:  # noqa: BLE001
        log.engine.error(
            "supervisor.synthesize_from_calls: falling back to minimal",
            exc_info=exc,
            extra={"_fields": {"n_calls": len(all_calls) if all_calls else 0, "lang": lang}},
        )
        return localize("self_heal_floor_minimal", lang)
