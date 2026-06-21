"""make_budget_callback — the per-iteration budget gate (E2-S4).

Returned as on_iteration_complete. On a governor breach: a present human gets an
in-memory clarify Raise/Stop (fail-closed: Stop / timeout / no-gateway → raise);
otherwise it raises BudgetBreach immediately. The exception carries the partial
work (last assistant text + tool calls) so execute can deliver a partial result.
Clarify lives HERE (execute layer), never on the provider stack.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from stackowl.exceptions import BudgetBreach
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover
    from stackowl.providers.react_callback import ReActIterationState

_RAISE = "Raise"
_STOP = "Stop"
_WAIT_TIMEOUT_S = 120.0


def resolve_clarify_wait_timeout(channel: str, settings: Any) -> float:
    """Resolve the per-channel clarify Raise/Stop wait timeout (STEER-7/F094).

    Accepts EITHER a :class:`ClarifySettings` directly OR a root ``Settings``
    object (whose ``.clarify`` is unwrapped). A ``per_channel`` override for
    ``channel`` wins, else the global ``wait_timeout_s`` (default 120s). Pure and
    fail-safe — a missing/odd settings object or a non-positive configured value
    falls back to the 120s default and NEVER raises (a broken config must not
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


def make_budget_callback(
    governor: Any,
    *,
    interactive: bool,
    clarify: Any,
    session_id: str,
    channel: str,
    wait_timeout_s: float = _WAIT_TIMEOUT_S,
) -> Callable[[ReActIterationState], Awaitable[list[dict[str, Any]] | None]]:
    """Return an async callback that gates each ReAct iteration against budget caps.

    Args:
        governor: BudgetGovernor (or duck-typed stub) with check()/raise_caps().
        interactive: True when there is a present human who can respond to a
            clarify prompt (e.g. CLI/Telegram session, not a headless run).
        clarify: ClarifyGateway instance or None.  Must be non-None when
            interactive=True to enable the Raise/Stop round-trip.
        session_id: Propagated to clarify.ask() for routing.
        channel: Channel identifier propagated to clarify.ask().
        wait_timeout_s: Seconds to wait for a human answer before failing closed.

    Returns:
        An async callable ``(iter_state: ReActIterationState) -> None``.
        Returns ``None`` always (it folds no messages — Task 9 splice contract;
        it is a pure side-effect/gate callback) on no breach, or raises
        BudgetBreach (breach + partial).
    """

    async def _gate(iter_state: ReActIterationState) -> list[dict[str, Any]] | None:
        # tool_call_records is the cumulative snapshot of all dispatches this turn,
        # so the step cap counts individual tool calls (not just ReAct rounds).
        breach = governor.check(
            iter_state.iteration, tool_calls=len(iter_state.tool_call_records)
        )
        if breach is None:
            return None  # no breach — fold nothing (Task 9 splice contract)

        log.engine.debug(
            "[budget] gate: breach detected",
            extra={"_fields": {"cap": breach.cap, "limit": breach.limit,
                               "actual": breach.actual, "interactive": interactive}},
        )

        if interactive and clarify is not None:
            try:
                cid = await clarify.ask(
                    session_id,
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
