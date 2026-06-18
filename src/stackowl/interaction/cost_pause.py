"""CostPauseGuard — soft per-turn cost pause via the E5 clarify round-trip.

When a single INTERACTIVE turn's accumulated LLM spend crosses a soft budget
(``BudgetSettings.per_turn_pause_usd``), the assistant ASKS the user
("This turn has spent about $X so far. Continue?") via the
:class:`~stackowl.interaction.clarify_gateway.ClarifyGateway` BEFORE running any
further expensive operation (delegation fan-out / mixture-of-agents). The user's
tap decides: ``Stop`` aborts the expensive op (a clean structured refusal at the
call site, never a raise); ``Continue`` proceeds. This is a SOFT pause — distinct
from ``BudgetSettings.daily_limit_usd``, the hard cap that raises in
:class:`~stackowl.providers.cost_tracker.CostTracker`. The two coexist.

The guard is interactive-only by nature: it can only ask a present human. A
NON-interactive run (cron / heartbeat / parliament / delegation child) is NEVER
paused — it returns ``True`` (continue) unchanged, so background flows keep their
existing behavior (the daily hard cap still applies there).

**Self-healing / fail-OPEN ([[feedback_always_self_healing]]).** The pause must
NEVER wedge a turn on the pause machinery itself. Any of: feature disabled
(threshold ``None``), under threshold, non-interactive, already-asked-this-turn,
no clarify gateway wired, a gateway error, a delivery/park failure, a timeout, or
a "Continue"/no-channel answer → return ``True`` (continue). ONLY an explicit
``Stop`` answer returns ``False``. No error is swallowed silently — every
degradation is logged (B5) before fail-OPEN.

A turn is asked AT MOST ONCE: the first crossing prompts; subsequent expensive
ops in the same turn proceed without re-prompting (the bounded ``_asked`` set,
FIFO-evicted past a cap so it cannot grow unbounded over server lifetime).

Provenance: StackOwl-native — composes the existing CostTracker running total +
ClarifyGateway suspend/resume. No external port.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.infra.trace import TraceContext

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.interaction.clarify_gateway import ClarifyGateway
    from stackowl.pipeline.services import StepServices
    from stackowl.providers.cost_tracker import CostTracker

# The two tapped choices. "Stop" is the ONLY answer that aborts; anything else
# (Continue / timeout / no answer) fails OPEN to continue.
_CHOICE_CONTINUE = "Continue"
_CHOICE_STOP = "Stop"

# Bound on the per-trace already-asked set so it cannot grow without limit across
# a long-lived server process (one entry per turn that crossed the budget).
_MAX_ASKED_TURNS = 4096

# How long the pause parks waiting for the user's tap before failing OPEN. A
# generous default — a parked asyncio waiter is cheap and the gateway loop stays
# free while we wait — but bounded so a never-answered pause eventually continues
# rather than wedging the turn forever.
_DEFAULT_WAIT_TIMEOUT_S = 600.0


class CostPauseGuard:
    """Gate the expensive paths behind a soft per-turn cost pause (clarify)."""

    def __init__(
        self,
        *,
        cost_tracker: CostTracker,
        clarify_gateway: ClarifyGateway | None,
        threshold_usd: float | None,
        wait_timeout_s: float = _DEFAULT_WAIT_TIMEOUT_S,
        clock: Clock | None = None,
    ) -> None:
        """Wire the guard to the live CostTracker + ClarifyGateway.

        ``threshold_usd`` is ``BudgetSettings.per_turn_pause_usd`` (``None`` →
        feature disabled → :meth:`gate` is a no-op that always continues).
        ``clarify_gateway`` may be ``None`` (no interactive channel wired) → the
        guard fails OPEN (continues) rather than wedging.
        """
        self._cost_tracker = cost_tracker
        self._clarify_gateway = clarify_gateway
        self._threshold_usd = threshold_usd
        self._wait_timeout_s = wait_timeout_s
        self._clock: Clock = clock or WallClock()
        # Bounded FIFO set of trace_ids already asked this server lifetime (the
        # value is unused — OrderedDict gives O(1) membership + FIFO eviction).
        self._asked: OrderedDict[str, None] = OrderedDict()
        log.gateway.debug(
            "cost_pause.init: entry",
            extra={
                "_fields": {
                    "threshold_usd": threshold_usd,
                    "has_clarify_gateway": clarify_gateway is not None,
                    "wait_timeout_s": wait_timeout_s,
                }
            },
        )

    def update_threshold(self, threshold_usd: float | None) -> None:
        """Hot-reload the soft per-turn threshold (ConfigWatcher on reload)."""
        log.gateway.info(
            "cost_pause.update_threshold: %s -> %s",
            self._threshold_usd,
            threshold_usd,
            extra={
                "_fields": {
                    "old_threshold_usd": self._threshold_usd,
                    "new_threshold_usd": threshold_usd,
                }
            },
        )
        self._threshold_usd = threshold_usd

    async def gate(
        self,
        *,
        trace_id: str,
        session_id: str,
        channel: str,
        interactive: bool,
    ) -> bool:
        """Return ``True`` to continue the expensive op, ``False`` to abort (Stop).

        Fast no-pause paths (all return ``True``): threshold ``None`` (disabled),
        turn cost under threshold, non-interactive run, or this turn was already
        asked. Otherwise marks the turn asked and runs the clarify round-trip;
        only an explicit ``Stop`` returns ``False``. Self-healing: any error /
        missing gateway / timeout fails OPEN (``True``). Never raises.
        """
        # 1. ENTRY
        threshold = self._threshold_usd
        cost = self._cost_tracker.turn_cost_usd(trace_id)
        log.gateway.debug(
            "cost_pause.gate: entry",
            extra={
                "_fields": {
                    "trace_id": trace_id,
                    "interactive": interactive,
                    "threshold_usd": threshold,
                    "turn_cost_usd": cost,
                }
            },
        )

        # 2. DECISION — the no-pause fast paths (feature/threshold/interactivity).
        if threshold is None or threshold <= 0:
            return True
        if cost < threshold:
            return True
        if not interactive:
            log.gateway.debug(
                "cost_pause.gate: over threshold but non-interactive — continue",
                extra={"_fields": {"trace_id": trace_id, "turn_cost_usd": cost}},
            )
            return True
        if trace_id in self._asked:
            log.gateway.debug(
                "cost_pause.gate: already asked this turn — continue",
                extra={"_fields": {"trace_id": trace_id, "turn_cost_usd": cost}},
            )
            return True

        gateway = self._clarify_gateway
        if gateway is None or not session_id or not channel:
            # Self-healing fail-OPEN: no way to ask → never wedge the turn.
            log.gateway.warning(
                "cost_pause.gate: cannot ask (no gateway/channel) — fail-open continue",
                extra={
                    "_fields": {
                        "trace_id": trace_id,
                        "has_gateway": gateway is not None,
                        "has_session": bool(session_id),
                        "has_channel": bool(channel),
                    }
                },
            )
            return True

        # Mark asked BEFORE the round-trip so a concurrent second expensive op in
        # the same turn does not double-prompt while this one is parked.
        self._mark_asked(trace_id)

        return await self._ask(
            gateway=gateway,
            trace_id=trace_id,
            session_id=session_id,
            channel=channel,
            cost=cost,
        )

    # ---------------------------------------------------------------- helpers

    async def _ask(
        self,
        *,
        gateway: ClarifyGateway,
        trace_id: str,
        session_id: str,
        channel: str,
        cost: float,
    ) -> bool:
        """Run the clarify suspend→ask→resume; map the answer to continue/stop.

        Self-healing: any exception, a timeout, or a non-``Stop`` answer fails
        OPEN (``True``). ONLY an explicit ``Stop`` returns ``False``.
        """
        question = f"This turn has spent about ${cost:.2f} so far. Continue?"
        try:
            # 3. STEP — register + deliver the pause as a BLOCKING ask, then park
            # on the waiter until the user taps (or we time out → fail-open).
            clarify_id = await gateway.ask(
                session_id,
                channel,
                question,
                choices=(_CHOICE_CONTINUE, _CHOICE_STOP),
                blocking=True,
            )
            answer, _outcome = await gateway.wait_for_answer(
                clarify_id, timeout=self._wait_timeout_s,
            )
        except Exception as exc:  # B5 self-healing — never wedge the turn on the pause
            log.gateway.error(
                "cost_pause.gate: clarify ask/wait failed — fail-open continue",
                exc_info=exc,
                extra={"_fields": {"trace_id": trace_id, "channel": channel}},
            )
            return True

        # 4. EXIT — ONLY an explicit "Stop" aborts; Continue / timeout / no answer
        # → fail-OPEN continue (never assume the user wants to stop).
        stop = answer is not None and answer.strip().casefold() == _CHOICE_STOP.casefold()
        log.gateway.info(
            "cost_pause.gate: resolved",
            extra={
                "_fields": {
                    "trace_id": trace_id,
                    "turn_cost_usd": cost,
                    "answered": answer is not None,
                    "stop": stop,
                }
            },
        )
        return not stop

    def _mark_asked(self, trace_id: str) -> None:
        """Record ``trace_id`` as asked; bounded FIFO eviction past the cap."""
        self._asked[trace_id] = None
        self._asked.move_to_end(trace_id)
        while len(self._asked) > _MAX_ASKED_TURNS:
            evicted_id, _ = self._asked.popitem(last=False)
            log.gateway.debug(
                "cost_pause._mark_asked: evicted oldest asked turn (bounded)",
                extra={"_fields": {"evicted_trace_id": evicted_id}},
            )


async def gate_or_continue(services: StepServices, *, action: str) -> bool:
    """Shared cost-pause gate for the expensive-op tools (B2: one site, two callers).

    Resolves the soft per-turn :class:`CostPauseGuard` off ``services`` and reads
    the live ``trace_id``/``session_id``/``channel``/``interactive`` off
    :class:`TraceContext` (tools never touch ``PipelineState``). Returns ``True`` to
    continue the expensive op (``action`` — e.g. ``"delegation"`` / ``"fan-out"``)
    and ``False`` ONLY when the user explicitly chose **Stop** at the pause.

    Self-healing: no guard wired → ``True`` (continue). The guard itself fails OPEN
    on every other degradation (disabled / under-budget / non-interactive / already-
    asked / gateway error / timeout), so this helper never wedges a turn and never
    raises. Used by both ``delegate_task`` and ``mixture_of_agents``.
    """
    guard = services.cost_pause_guard
    if guard is None:
        return True
    ctx = TraceContext.get()
    trace_id = str(ctx.get("trace_id") or "")
    proceed = await guard.gate(
        trace_id=trace_id,
        session_id=str(ctx.get("session_id") or ""),
        channel=str(ctx.get("channel") or ""),
        interactive=bool(ctx.get("interactive", False)),
    )
    if not proceed:
        log.gateway.info(
            "cost_pause.gate_or_continue: user chose Stop — aborting expensive op",
            extra={"_fields": {"trace_id": trace_id, "action": action}},
        )
    return proceed
