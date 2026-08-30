"""make_budget_callback — the per-iteration budget gate (E2-S4).

Returned as on_iteration_complete. On a governor breach: a present human gets an
in-memory clarify Raise/Stop (fail-closed: Stop / timeout / no-gateway → raise);
otherwise it raises BudgetBreach immediately. The exception carries the partial
work (last assistant text + tool calls) so execute can deliver a partial result.
Clarify lives HERE (execute layer), never on the provider stack.

It ALSO folds a one-shot convergence directive at ~75% of the step budget. Trace
f33c9fa0 ran 16 rounds into a 20-step cap having been told nothing about its budget,
and was then killed mid-thought. The directive costs no model call — it rides the
existing splice contract — and is a budget FRACTION rather than a fixed step
interval, because the measured evidence backs budget-awareness and rejects periodic
self-evaluation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.exceptions import BudgetBreach
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.providers.react_callback import ReActIterationState

# Fraction of the step budget consumed before the model is told to converge. 0.75
# leaves a quarter of the budget to actually act on the warning; earlier is noise on
# turns that were going to finish anyway, later leaves no room to change course.
#
# This is deliberately a BUDGET FRACTION and not a fixed step interval. Bakir proposed
# "check progress every 5 steps"; the measured evidence rejects the interval (planning
# frequency has a peak then declines, and the always-plan agent showed the highest
# backtracking) while confirming the underlying problem (agents are not budget-aware
# and DO change strategy when told the budget). A fraction scales with the cap; an
# interval of 5 means something different at max_steps=8 than at max_steps=200.
_CONVERGE_AT_FRACTION = 0.75

# How many consecutive iterations with NO plan movement before the turn is asked
# to re-plan. Bakir's number, 2026-08-29: "check of progress for every 5 steps.
# If agent did not move on task then optimize and replan actions again."
#
# WHAT THE EVIDENCE CHANGED, and it is only the ANSWERER. He proposed the model
# evaluate its own progress; measured, an LLM judging progress from its own
# trajectory scores 0.54-0.65 AUROC (near chance) and degrades as the trace grows
# — least reliable exactly when most needed — while a structural detector scores
# 0.83-0.95, and a judge-inclusive arm cost +129% tokens for identical quality.
# So the interval, the question and the replan are all his; the answer comes from
# COUNTING the plan's own item statuses, which the model already maintains. Free,
# deterministic, and it cannot hallucinate progress.
_NO_PROGRESS_STEPS = 5

# Never more than this many nudges in one turn. A directive repeated every step
# would grow context on precisely the turns already closest to the cap.
_MAX_PLAN_NUDGES = 3


def _plan_signature(counts: dict[str, int]) -> tuple[int, ...]:
    """The comparable shape of "where the plan is now".

    Deliberately the finished/active split rather than the raw dict: an item
    edited in place (content reworded, a new pending item appended) is not
    progress, and counting it as such would let a turn spin forever while looking
    busy. Movement means a status CHANGED.
    """
    return (
        int(counts.get("completed", 0)),
        int(counts.get("cancelled", 0)),
        int(counts.get("in_progress", 0)),
        int(counts.get("pending", 0)),
    )


_NO_PLAN_DIRECTIVE = (
    "[plan] You have taken {steps} steps and there is no plan for this turn. "
    "Write one now with the `todo` capability: the concrete steps you intend to "
    "take, then keep their status current as you go. Then continue — do not "
    "restate what you have already done."
)

_REPLAN_DIRECTIVE = (
    "[plan] No plan item has advanced in {steps} steps. The current approach is "
    "not working, so revise the plan rather than repeating it: update `todo` with "
    "what you have RULED OUT and why, and what you will do differently. Keep "
    "everything you have already established — do not start over. If the goal "
    "cannot be reached, say so and stop."
)

_RAISE = "Raise"
_STOP = "Stop"
# Raised 120.0 -> 600.0 on 2026-07-22 (owner decision): a human needs real time
# to see and respond to a Raise/Stop prompt, not just two minutes.
_WAIT_TIMEOUT_S = 600.0


def resolve_clarify_wait_timeout(channel: str, settings: Any) -> float:
    """Resolve the per-channel clarify Raise/Stop wait timeout (STEER-7/F094).

    Accepts EITHER a :class:`ClarifySettings` directly OR a root ``Settings``
    object (whose ``.clarify`` is unwrapped). A ``per_channel`` override for
    ``channel`` wins, else the global ``wait_timeout_s`` (default 600s). Pure and
    fail-safe — a missing/odd settings object or a non-positive configured value
    falls back to the 600s default and NEVER raises (a broken config must not
    auto-Stop the user; it degrades to the safe documented default).
    """
    try:
        if settings is None:
            return _WAIT_TIMEOUT_S
        # Unwrap a root Settings to its ClarifySettings; a ClarifySettings (which
        # has no nested ``.clarify``) is used as-is.
        clarify = getattr(settings, "clarify", None)
        if clarify is None:
            clarify = settings
        per_channel = getattr(clarify, "per_channel", {}) or {}
        if channel in per_channel:
            value = float(per_channel[channel])
            if value > 0.0:
                return value
        default = float(getattr(clarify, "wait_timeout_s", _WAIT_TIMEOUT_S))
        return default if default > 0.0 else _WAIT_TIMEOUT_S
    except Exception:  # noqa: BLE001 — never let a bad config crash/auto-Stop; use default
        return _WAIT_TIMEOUT_S


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


def _converge_directive(
    governor: Any,
    iter_state: ReActIterationState,
    n_calls: int,
    already_sent: bool,
) -> list[dict[str, Any]] | None:
    """Return the one-shot convergence directive, or None.

    Pure decision + message build. Never raises: a governor without
    ``steps_remaining`` (a duck-typed stub, of which this repo has several) simply
    gets no directive rather than losing its turn to an AttributeError.
    """
    if already_sent:
        return None
    try:
        remaining = governor.steps_remaining(iter_state.iteration, tool_calls=n_calls)
    except Exception as exc:  # noqa: BLE001 — advisory only; never cost a turn
        log.engine.debug(
            "[budget] converge: governor has no step budget to report",
            extra={"_fields": {"error": str(exc)}},
        )
        return None
    if remaining is None:
        return None

    done = max(iter_state.iteration + 1, n_calls)
    total = done + remaining
    if total <= 0 or remaining <= 0:
        return None
    if (done / total) < _CONVERGE_AT_FRACTION:
        return None

    log.engine.info(
        "[budget] converge: telling the model its budget is nearly spent",
        extra={"_fields": {"steps_done": done, "steps_total": total,
                           "steps_remaining": remaining}},
    )
    return [{
        "role": "user",
        "content": (
            f"[budget] {remaining} step(s) left of {total} for this turn. "
            "Stop exploring and converge now: answer with what you already have, "
            "and name anything you could not determine. Be concrete and brief. "
            "If one more tool call would complete the answer, make that call and "
            "then report."
        ),
    }]


def make_budget_callback(
    governor: Any,
    *,
    interactive: bool,
    clarify: Any,
    session_key: str,
    channel: str,
    wait_timeout_s: float = _WAIT_TIMEOUT_S,
    plan_counts: Callable[[], dict[str, int]] | None = None,
) -> Callable[[ReActIterationState], Awaitable[list[dict[str, Any]] | None]]:
    """Return an async callback that gates each ReAct iteration against budget caps.

    Args:
        governor: BudgetGovernor (or duck-typed stub) with check()/raise_caps().
        interactive: True when there is a present human who can respond to a
            clarify prompt (e.g. CLI/Telegram session, not a headless run).
        clarify: ClarifyGateway instance or None.  Must be non-None when
            interactive=True to enable the Raise/Stop round-trip.
        session_key: Propagated to clarify.ask() for routing.
        channel: Channel identifier propagated to clarify.ask().
        wait_timeout_s: Seconds to wait for a human answer before failing closed.

    Returns:
        An async callable ``(iter_state: ReActIterationState) -> ...``. It returns
        ``None`` on an ordinary round, raises ``BudgetBreach`` at the cap, and — ONCE
        per turn, at ``_CONVERGE_AT_FRACTION`` of the step budget — folds a single
        convergence directive via the Task 9 splice contract so the next LLM round
        observes it. It is no longer a pure side-effect callback; the fold is the
        cheapest available intervention (no model call) on a turn heading for the cap.
    """

    # Fires at most once per turn. Re-sending the directive every round after the
    # threshold would grow the transcript on exactly the turns already closest to the
    # cap — paying the accumulating context cost to repeat what the model has read.
    converge_sent = False
    # Progress-evaluation state: the last plan shape we saw, the iteration it was
    # first seen at, and how many nudges this turn has already been given.
    last_signature: tuple[int, ...] | None = None
    unchanged_since = 0
    plan_nudges = 0

    def _progress_directive(iteration: int) -> list[dict[str, Any]] | None:
        """Bakir's every-5-steps evaluation, answered structurally. Never raises."""
        nonlocal last_signature, unchanged_since, plan_nudges
        if plan_counts is None or plan_nudges >= _MAX_PLAN_NUDGES:
            return None
        try:
            counts = plan_counts() or {}
            signature = _plan_signature(counts)
            total = int(counts.get("total", 0))
        except Exception as exc:  # noqa: BLE001 — advisory; never cost a turn
            log.engine.debug(
                "[plan] progress check unavailable — skipping",
                extra={"_fields": {"error": str(exc)}},
            )
            return None

        if signature != last_signature:
            last_signature = signature
            unchanged_since = iteration
            return None

        steps_still = iteration - unchanged_since + 1
        if steps_still < _NO_PROGRESS_STEPS:
            return None

        # Reset the window so the next nudge is another full interval away, not
        # the very next step.
        unchanged_since = iteration
        plan_nudges += 1
        no_plan = total == 0
        log.engine.info(
            "[plan] no progress — asking the turn to %s",
            "plan" if no_plan else "re-plan",
            extra={"_fields": {
                "steps_without_progress": steps_still,
                "plan_items": total, "nudge": plan_nudges,
                "kind": "no_plan" if no_plan else "replan",
            }},
        )
        template = _NO_PLAN_DIRECTIVE if no_plan else _REPLAN_DIRECTIVE
        return [{"role": "user", "content": template.format(steps=steps_still)}]

    async def _gate(iter_state: ReActIterationState) -> list[dict[str, Any]] | None:
        nonlocal converge_sent
        # tool_call_records is the cumulative snapshot of all dispatches this turn,
        # so the step cap counts individual tool calls (not just ReAct rounds).
        n_calls = len(iter_state.tool_call_records)
        breach = governor.check(iter_state.iteration, tool_calls=n_calls)
        if breach is None:
            # Not out of budget — but is it nearly out? Trace f33c9fa0 ran 16 rounds
            # into a 20-step cap and nothing ever told it to converge; it was killed
            # mid-thought having delivered nothing. Telling it costs no model call:
            # the fold contract splices this into `messages` before the next round.
            folded = _converge_directive(governor, iter_state, n_calls, converge_sent)
            if folded is not None:
                converge_sent = True
                return folded
            # The evaluation pipeline: has the plan actually moved? Structural,
            # free, and it runs on every ordinary iteration.
            progress = _progress_directive(iter_state.iteration)
            if progress is not None:
                return progress
            return None  # no breach — fold nothing (Task 9 splice contract)

        log.engine.debug(
            "[budget] gate: breach detected",
            extra={"_fields": {"cap": breach.cap, "limit": breach.limit,
                               "actual": breach.actual, "interactive": interactive}},
        )

        if interactive and clarify is not None:
            try:
                cid = await clarify.ask(
                    session_key,
                    channel,
                    f"Budget cap '{breach.cap}' reached (limit {breach.limit}, used "
                    f"{breach.actual}). Raise or Stop?",
                    choices=(_RAISE, _STOP),
                    blocking=True,
                )
                answer, _ = await clarify.wait_for_answer(cid, timeout=wait_timeout_s)
            except Exception as exc:  # noqa: BLE001 — fail-closed: any clarify error → Stop
                log.engine.warning(
                    "[budget] gate: clarify error — stopping",
                    extra={"_fields": {"cap": breach.cap, "error": str(exc)}},
                )
                answer = None

            if answer is not None and answer.strip().casefold() == _RAISE.casefold():
                governor.raise_caps(breach.cap)
                log.engine.info(
                    "[budget] gate: human raised cap — continuing",
                    extra={"_fields": {"cap": breach.cap}},
                )
                return None  # cap raised — fold nothing (Task 9 splice contract)

        log.engine.warning(
            "[budget] gate: cap reached — stopping",
            extra={"_fields": {"cap": breach.cap, "limit": breach.limit,
                               "actual": breach.actual}},
        )
        raise BudgetBreach(
            breach.cap,
            breach.limit,
            breach.actual,
            partial_text=_last_assistant_text(iter_state.messages),
            tool_call_records=list(iter_state.tool_call_records),
        )

    return _gate
