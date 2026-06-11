"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE, is_structural_giveup
from stackowl.setup.localize import localize, localize_format

_ERROR_MAX_LEN = 500


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
    *, judge_directive: str | None, all_calls: list[dict[str, object]], draft: str
) -> str | None:
    """Always-on structural veto over the judge's verdict.

    If the judge already returned a directive (it flagged a give-up), keep it.
    Otherwise compute the structural signal from the AUTHORITATIVE ``failed`` bools;
    if it's a give-up, OVERRIDE the judge's (possibly hallucinated) DELIVERED and
    inject the persistence directive. Catches a weak local judge returning a
    confident-but-wrong "delivered" — the actual Jetson failure mode.
    """
    if judge_directive is not None:
        return judge_directive
    failures, successes = tally_tool_outcomes(all_calls)
    if is_structural_giveup(tool_failures=failures, successful_tool_calls=successes, draft=draft):
        log.engine.debug("supervisor.veto: overriding judge DELIVERED on structural give-up")
        return PERSISTENCE_DIRECTIVE
    return None


def decide_nudge(
    *,
    judge_directive: str | None,
    all_calls: list[dict[str, object]],
    draft: str,
    nudge_budget: int,
    calls_at_last_nudge: int | None,
) -> tuple[str | None, int, int | None]:
    """Decide whether to nudge, applying the veto THEN the escalation-reward cap.

    Pure; never raises. Reused by every provider's enforce loop (anthropic now,
    openai in a later task) so the self-heal budget logic lives in ONE place.

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
    directive = apply_structural_veto(
        judge_directive=judge_directive, all_calls=all_calls, draft=draft
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
