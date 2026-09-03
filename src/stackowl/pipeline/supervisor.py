"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations

from stackowl.infra.observability import log
from stackowl.owls.base_prompt import strip_turn_context
from stackowl.pipeline.persistence import (
    CAPABILITY_GAP_DIRECTIVE,
    PERSISTENCE_DIRECTIVE,
    is_structural_giveup,
)
from stackowl.setup.localize import (
    explain_failure_class,
    localize,
    localize_format,
)

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
       :func:`~stackowl.pipeline.delivery_gate.is_consequential_giveup_now`.

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
    :func:`~stackowl.pipeline.delivery_gate.is_consequential_giveup_now`, which
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
    failure_class: str | None = None,
    lang: str = "en",
    lean: bool = False,
) -> str:
    """Pure, deterministic never-empty honest floor message — NO model, NO await, NO I/O.

    The TerminalResponseGuarantee core synthesizer. Builds an honest "couldn't
    finish" message from whatever turn data survived, via
    :func:`localize_format`. Guarantees a non-empty string on ANY exit path: on
    any error (including ``None`` inputs causing issues) it returns the static
    localized minimal fallback. NEVER raises, NEVER returns empty.

    ``failed_capability`` — when ``None`` it is derived from ``attempts[0]``;
    :func:`synthesize_from_calls` passes the precise failed tool name.

    ``lean`` — when ``True`` (model window ≤ ``LEAN_WINDOW_THRESHOLD``), a
    capability-honest suffix is appended via the localization layer
    (``self_heal_floor_lean_suffix``). When ``False`` (default), output is
    BYTE-IDENTICAL to the previous behaviour — no suffix is ever added.

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
                "lean": lean,
            }
        },
    )
    try:
        attempts_list = list(attempts) if attempts else []
        derived_capability = failed_capability
        if derived_capability is None:
            derived_capability = attempts_list[0] if attempts_list else ""
        # COMPOSED PER SENTENCE, NOT CHOSEN AS A SHAPE. The floor used to pick one
        # of three whole-message templates, but (capability?, attempts?, error?)
        # has EIGHT shapes and only three had a template — so the other five fell
        # through to the five-slot one and rendered its missing sentences as bare
        # punctuation. What Bakir received: "The capability that failed: . What I
        # tried: .  Technical detail: ". MEASURED 2026-09-03 by running all eight
        # through this function: 001, 011, 100, 101 and 110 were all broken, and
        # 81 of the 133 floors sent since the 2026-08-15 repairs still carried a
        # blank. Those repairs ADDED two templates; the eighth would not have
        # fixed the ninth case. Emitting only the sentences that have data leaves
        # no combination to miss.
        # AND THERE IS EXACTLY ONE EXIT. The graceful "nothing survived" message
        # used to be an EARLY RETURN, which is the very shape this fix exists to
        # remove — a branch that hands back a finished message skips whatever the
        # later stages would have added. It had already caused a second, unnoticed
        # defect: the lean-window suffix is appended AFTER that return, so a
        # graceful floor on a small model never carried it, and no test noticed
        # because the only lean test asserts the suffix is ABSENT. Graceful is now
        # a sentence chosen by the same emit-if-present rule, and the lean suffix
        # and the never-empty check both live on the single path below.
        detail: list[str] = []
        if derived_capability:
            detail.append(localize_format(
                "self_heal_floor_s_capability", lang,
                failed_capability=derived_capability,
            ))
        if attempts_list:
            detail.append(localize_format(
                "self_heal_floor_s_attempts", lang,
                attempts=", ".join(attempts_list),
            ))
        if partial and partial.strip():
            detail.append(partial.strip() + " ")
        if error:
            # SAY WHAT HAPPENED BEFORE SAYING WHAT BROKE. The class-to-prose
            # catalogue was reached only from delivery_gate's neutral fallback,
            # so this renderer handed him the raw exception text instead: on
            # 2026-09-03 the same outage produced "Technical detail: Provider
            # 'NeraAiRaw' error: Connection error." on two attempts and a plain
            # explanation on the third, thirty seconds apart. The detail is KEPT
            # — he is the engineer here — it just stops being the whole answer.
            cause = explain_failure_class(failure_class, lang)
            if cause:
                detail.append(cause + " ")
            detail.append(localize_format("self_heal_floor_s_error", lang, error=error))
        elif attempts_list and not derived_capability:
            # Tried things, nothing reported a failure. Saying so is the honest
            # answer; naming attempts[0] would accuse a tool that may have worked.
            detail.append(localize("self_heal_floor_s_no_attribution", lang))

        if detail:
            # The lead sentence carries the goal, or stands alone when the goal did
            # not survive strip_turn_context.
            lead = (
                localize_format("self_heal_floor_s_goal", lang, goal=goal)
                if goal and goal.strip()
                else localize("self_heal_floor_s_nogoal", lang)
            )
            pieces = [lead, *detail]
        else:
            # Nothing survived at all — the warm, slot-free message.
            pieces = [localize("self_heal_floor_graceful", lang)]
        log.engine.debug(
            "supervisor.synthesize_floor: composed",
            extra={"_fields": {
                "lang": lang, "sentences": len(pieces), "graceful": not detail,
                "has_goal": bool(goal and goal.strip()),
                "has_capability": bool(derived_capability),
                "n_attempts": len(attempts_list), "has_error": bool(error),
            }},
        )
        result = "".join(pieces).strip()
        if not result:
            raise ValueError("empty floor result")
        # Lean-window capability-honest suffix: appended only when lean=True so
        # that lean=False output is BYTE-IDENTICAL to the previous behaviour.
        if lean:
            suffix = localize("self_heal_floor_lean_suffix", lang)
            if suffix:
                result = result + " " + suffix
                log.engine.debug(
                    "supervisor.synthesize_floor: lean suffix appended",
                    extra={"_fields": {"lang": lang, "suffix_len": len(suffix)}},
                )
        log.engine.debug(
            "supervisor.synthesize_floor: exit",
            extra={"_fields": {"result_len": len(result), "lean": lean}},
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

    Derives the precise ``failed_capability`` from the LAST recorded outcome
    per (tool name, target arg) pair -- so a retried call that eventually
    succeeds (e.g. ``owl_build`` create->create->edit, edit succeeding) is
    never reported as failed just because an earlier attempt on the same
    capability was. The ``attempts`` list and failing ``error`` are derived
    the same way, then delegated to :func:`synthesize_floor`. Pure; never
    raises.
    """
    log.engine.debug(
        "supervisor.synthesize_from_calls: entry",
        extra={"_fields": {"n_calls": len(all_calls) if all_calls else 0, "lang": lang}},
    )
    try:
        calls = list(all_calls) if all_calls else []
        # Key on (tool name, target arg) -- dict insertion keeps first-seen
        # order, but each re-assignment overwrites the value, so the value
        # left per key after the loop is that key's LAST call in the turn.
        last_by_key: dict[tuple[str, str], dict[str, object]] = {}
        for c in calls:
            name = str(c.get("name") or "")
            args = c.get("args")
            target = str(args.get("name") or "") if isinstance(args, dict) else ""
            last_by_key[(name, target)] = c

        # Stays "" when nothing failed, and that is deliberate. Deriving a name
        # from attempts[0] here would report a capability that SUCCEEDED as the
        # one that failed — exactly what
        # test_synthesize_from_calls_last_outcome_overrides_earlier_retry_failure
        # exists to prevent (owl_build create->create->edit, edit succeeding).
        # The empty slot Bakir saw is fixed by omitting the SENTENCE, below, not
        # by inventing a culprit: this message's only job is to be honest.
        failed_capability = ""
        error = ""
        for c in last_by_key.values():
            if bool(c.get("failed")):
                failed_capability = str(c.get("name") or "")
                error = str(c.get("result") or "")[:_ERROR_MAX_LEN]
                break
        attempts = [str(c.get("name") or "") for c in calls]
        return synthesize_floor(
            # The provider hands us the COMPOSED turn text, which carries the
            # volatile context D01.1 prepends to the user's message. This is the
            # path that actually reached Bakir on 2026-08-15 — the strips added
            # to delivery_gate and delegate_task do not cover it.
            strip_turn_context(goal),
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
