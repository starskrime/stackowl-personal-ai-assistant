"""LLMGateway — the single escalating LLM interface.

Every LLM consumer goes through this instead of obtaining a provider from the
registry and invoking it directly. A request starts at the ``floor`` tier
(default ``"fast"``) and escalates ``fast → standard → powerful`` — bounded by
``ceiling`` — whenever the model itself signals the task is beyond it via the
in-band ``ESCALATE`` sentinel.

This adds **complexity-based** escalation. The registry's existing
**failure-based** fallback (skip a provider whose circuit is OPEN) still runs
UNDERNEATH via ``resolve_tier_with_fallback`` — the two compose: escalation picks
the target tier, failure-fallback finds a live provider for it.

Meta-calls that must not recurse (the intent router, the give-up judges) pin
``floor == ceiling`` to a single tier: same uniform interface, no escalation.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from stackowl.exceptions import CircuitOpenError, RateLimitError
from stackowl.infra import retry_ledger
from stackowl.infra.observability import log
from stackowl.providers._resilient_round import (
    RecoveryAction,
    is_provider_fault,
    recovery_for,
)
from stackowl.providers.base import CompletionResult, Message

if TYPE_CHECKING:
    from stackowl.providers.registry import ProviderRegistry

# The escalation ladder, least → most capable. ``local`` is a separate axis
# (offline backend), NOT part of the quality ladder.
LADDER: tuple[str, ...] = ("fast", "standard", "powerful")


def _retry_same_tier_enabled() -> bool:
    """ADR-2 flag read (``provider_retry_same_tier_once``). Fail-safe to True (the
    owner-approved default) on any config error — a flag read must never break a turn.
    Consulted ONLY on a fault path, so the happy path never constructs Settings here."""
    try:
        from stackowl.config.settings import cached_settings

        return bool(cached_settings().provider_retry_same_tier_once)
    except Exception:  # noqa: BLE001 — a flag read must never raise into the gateway
        return True

ESCALATE_SENTINEL = "ESCALATE"

# Appended to the system prompt for any attempt below the ceiling. Kept short and
# unambiguous; the model emits the bare sentinel ONLY when it truly cannot answer.
ESCALATE_INSTRUCTION = (
    "\n\nIf this request is genuinely beyond your capability to answer well — it "
    "needs deeper reasoning, broader knowledge, or more skill than you have — reply "
    "with EXACTLY the single word ESCALATE and nothing else. It will be routed to a "
    "more capable model. Only do this when you truly cannot produce a good answer; "
    "otherwise answer normally."
)


def is_cascadable_fault(exc: BaseException) -> bool:
    """True when ``exc`` is a provider fault the gateway should CASCADE past.

    This is the failure-fallback twin of the registry's complexity escalation: when
    a resolved provider call raises, the gateway climbs to the next tier instead of
    dead-ending at the user (F-16/F-17).

    Reuses ``_resilient_round.is_provider_fault`` (transport/5xx/429/wrapped-cause
    classification) and ADDS the two breaker-control signals it deliberately
    excludes — :class:`CircuitOpenError` (the breaker short-circuited this provider)
    and :class:`RateLimitError` (a deliberate cap refusal). Those two are NOT
    recorded against the breaker, but for ROUTING they are exactly the faults a
    higher tier should recover. Control-flow / our-own-bug errors (user-stop,
    budget-kill, malformed-args ValueError, an arbitrary RuntimeError) stay False so
    they propagate immediately, never masked by a silent fallback.

    D02.6 adds a third source: a cause whose prescribed recovery is to try a
    DIFFERENT model or a DIFFERENT credential. ``is_provider_fault`` says False for
    both (the provider is healthy; this request is simply unusable against it), so
    a 404 bad-model-id or a 402 out-of-credit used to dead-end at the user even
    with other tiers configured. Climbing the ladder IS that recovery — the next
    tier is a different model behind a different credential. Nothing else in the
    process can perform either action today, so this is where they land.
    """
    if isinstance(exc, (CircuitOpenError, RateLimitError)):
        return True
    if recovery_for(exc) in (RecoveryAction.FALLBACK_MODEL, RecoveryAction.ROTATE_CREDENTIAL):
        return True
    return is_provider_fault(exc)


def is_escalate_signal(text: str | None) -> bool:
    """True when a model response is the bare escalation sentinel (tolerant of
    surrounding whitespace / trailing punctuation, case-insensitive)."""
    if not text:
        return False
    cleaned = text.strip().strip(".!?\"'` ").upper()
    return cleaned == ESCALATE_SENTINEL


def tier_span(floor: str, ceiling: str) -> list[str]:
    """The ladder slice [floor..ceiling] inclusive (clamped, order-safe).

    Unknown tiers fall back to the full ladder bound so a typo never silently
    yields an empty span (which would mean "never call a model").
    """
    try:
        lo = LADDER.index(floor)
    except ValueError:
        lo = 0
    try:
        hi = LADDER.index(ceiling)
    except ValueError:
        hi = len(LADDER) - 1
    if hi < lo:
        hi = lo  # a ceiling below the floor degenerates to a single tier
    return list(LADDER[lo : hi + 1])


def escalation_target_is_identical(
    provider: Any, model: str, next_provider: Any, next_model: str
) -> bool:
    """True when stepping up a tier lands on the SAME backend AND the same model.

    MEASURED 2026-08-21. The ladder is fully wired — 25 ESCALATE_SENTINEL returns
    across the retained logs, 25 executions of the handler that consumes them — and
    every one of the 25 was ``standard -> powerful`` on a deployment with ONE enabled
    provider spanning all three tiers. So each discarded a finished attempt (14, 15,
    16 and 19 tool calls in four of those turns) and re-ran the identical loop against
    the identical model. ``on_escalate`` also resets the tool-outcome ledger, so the
    re-run is blind to what the first attempt learned.

    Nothing noticed, because the escalation log line never compared the target it was
    stepping up TO. This predicate is what makes that comparison possible.

    Compared by NAME, not by object identity: the registry may hand back distinct
    instances across calls, and it is the target that matters, not the Python object.
    Returns False when the next provider cannot be resolved — an unresolvable tier is
    a different problem and must not be reported as a no-op escalation.
    """
    if next_provider is None or provider is None:
        return False
    return (
        str(getattr(provider, "name", "") or "")
        == str(getattr(next_provider, "name", "") or "")
        and model == next_model
    )


class LLMGateway:
    """Stateless escalating wrapper over a :class:`ProviderRegistry`."""

    def _can_escalate_meaningfully(
        self, tiers: tuple[str, ...] | list[str], idx: int, provider: Any, model: str
    ) -> bool:
        """Is there a HIGHER tier that actually resolves somewhere else?

        ESC-22, ANSWERED BY BAKIR 2026-08-21: "deliver the floor already earned".

        `can_escalate` used to mean only "this is not the last rung", which is a fact
        about the LADDER, not about the TARGET. On a deployment where one provider
        spans every tier — which is this one — every rung resolves to the same
        (provider, model), so the flag said "a stronger model exists" when none did.

        The cost was measured before it was fixed: 25 escalations, all landing on the
        same (NeraAiRaw, neraai-v1-raw). Each made the provider DISCARD a finished
        attempt — 14, 15, 16 and 19 tool calls in four of those turns — return the
        ESCALATE sentinel, and hand the gateway a turn it had to re-run before anything
        could be delivered. `on_escalate` also reset the tool-outcome ledger, so the
        re-run was blind to what the first attempt had already learned.

        Telling the provider the truth up front is the whole fix. With this False, it
        never emits the sentinel: it takes its own floor path and DELIVERS the answer
        it just worked for, on the first attempt. Nothing downstream changes, because
        this is exactly the state every ladder already reaches at its ceiling.

        Resolution is a registry dict lookup, so scanning the tiers above costs
        nothing worth measuring.
        """
        for higher in tiers[idx + 1:]:
            try:
                nxt_provider, nxt_model, _degraded = (
                    self._registry.resolve_tier_with_fallback(higher)
                )
            except Exception as exc:  # noqa: BLE001 — never let a probe end a turn
                log.engine.warning(
                    "[llm_gateway] could not resolve a higher tier while checking "
                    "whether escalation can change anything — assuming it can",
                    exc_info=exc, extra={"_fields": {"tier": higher}},
                )
                return True
            if not escalation_target_is_identical(
                provider, model, nxt_provider, nxt_model
            ):
                return True
        return False

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    # -- non-tool completion ------------------------------------------------- #

    async def _retry_same_tier_once(
        self, attempt: Callable[[], Awaitable[Any]], tier: str, purpose: str
    ) -> Any | None:
        """ADR-2 — retry the SAME tier ONCE on a transient provider fault, via the one
        RecoveryActuator, BEFORE cascading. Returns the recovered result, or ``None`` if the
        retry also failed (the caller then cascades / re-raises as before). The completion is
        idempotent (no side effect), so re-running it cannot double-commit. Records nothing —
        the gateway owns the escalation trace; this is a transparent in-tier heal."""
        from stackowl.pipeline.recovery_actuator import Failure, RecoveryActuator

        failure = Failure(name=f"provider:{tier}", kind="provider", transient=True)
        outcome = await RecoveryActuator().recover(
            failure, attempt=attempt, verify=lambda r: r is not None, record=False,
        )
        if outcome.recovered:
            log.engine.info(
                "[llm_gateway] same-tier retry recovered a transient provider fault",
                extra={"_fields": {"purpose": purpose, "tier": tier}},
            )
            retry_ledger.record_retry(kind="same_tier_retry", provider=tier, detail=purpose)
            return outcome.result
        return None

    async def complete(
        self,
        messages: list[Message],
        *,
        floor: str = "fast",
        ceiling: str = "powerful",
        purpose: str = "",
        **kwargs: Any,
    ) -> CompletionResult:
        """Escalating non-streaming completion.

        Tries ``floor`` first; on the ESCALATE sentinel steps up the ladder to
        ``ceiling``. Preserves the cost-tracker + resilience already injected on
        each provider (we call the same ``provider.complete``).
        """
        tiers = tier_span(floor, ceiling)
        result: CompletionResult | None = None
        retried_tiers: set[str] = set()  # ADR-2 — one same-tier retry per tier per call
        for idx, tier in enumerate(tiers):
            provider, model, degraded = self._registry.resolve_tier_with_fallback(tier)
            # Same rule as the tool loop (ESC-22): "can escalate" must mean the target
            # above is DIFFERENT, not merely that a rung exists. Here it also decides
            # whether ESCALATE_INSTRUCTION is appended to the prompt at all — so on a
            # single-backend ladder the model is no longer invited to emit a sentinel
            # that cannot route anywhere.
            can_escalate = idx < len(tiers) - 1 and self._can_escalate_meaningfully(
                tiers, idx, provider, model
            )
            msgs = _augment_messages(messages, can_escalate)
            try:
                result = await provider.complete(msgs, model=model, **kwargs)
            except BaseException as exc:
                # ADR-2 — same-tier retry-once on a TRANSIENT fault, via the RecoveryActuator,
                # BEFORE cascading (flagged provider_retry_same_tier_once). A recovered retry
                # sets `result` and falls through to the escalate-check + return; if the retry
                # also fails (or the flag is off), the original cascade/re-raise logic runs.
                if (
                    is_cascadable_fault(exc)
                    and _retry_same_tier_enabled()
                    and tier not in retried_tiers
                ):
                    retried_tiers.add(tier)
                    retried = await self._retry_same_tier_once(
                        partial(provider.complete, msgs, model=model, **kwargs),
                        tier, purpose,
                    )
                    # success on same-tier retry → result set; flow to the escalate-check.
                    result = retried
                else:
                    retried = None
                    result = None
                if retried is None:
                    # F-16/F-17: a CLASSIFIED provider fault (circuit OPEN, rate-limit cap,
                    # 5xx/429, transport timeout) must cascade to the next tier — not
                    # dead-end at the user. A non-fault (our bug / user-stop / budget-kill)
                    # or the LAST tier re-raises, preserving today's terminal behaviour.
                    if not (can_escalate and is_cascadable_fault(exc)):
                        # F-19 — a fault outcome that ends the turn must be EXPLAINABLE,
                        # not a silent re-raise (only when the exception IS a classified
                        # fault; a control-flow signal / our-own bug propagates unannotated).
                        if is_cascadable_fault(exc):
                            log.engine.warning(
                                "[llm_gateway] complete: provider fault not recoverable — re-raising",
                                extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                                   "model": model,
                                                   "exc_type": type(exc).__name__,
                                                   "degraded_from": degraded,
                                                   "at_ceiling": not can_escalate}},
                            )
                        raise
                    log.engine.warning(
                        "[llm_gateway] complete: tier failed — falling back",
                        extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                           "to_tier": tiers[idx + 1], "model": model,
                                           "exc_type": type(exc).__name__,
                                           "degraded_from": degraded}},
                    )
                    retry_ledger.record_retry(
                        kind="tier_escalation", provider=tier,
                        detail=f"{tier}->{tiers[idx + 1]}:{type(exc).__name__}",
                    )
                    continue
            # Reached only on a try success OR a recovered same-tier retry → result is set.
            assert result is not None
            if can_escalate and is_escalate_signal(result.content):
                log.engine.info(
                    "[llm_gateway] complete: model escalated — stepping up tier",
                    extra={"_fields": {"purpose": purpose, "from_tier": tier, "model": model,
                                       "to_tier": tiers[idx + 1]}},
                )
                continue
            return result
        # Unreachable in practice (tier_span never empty); satisfy the type.
        assert result is not None
        return result

    # -- agentic tool loop --------------------------------------------------- #

    async def complete_with_tools(
        self,
        *,
        user_text: str,
        system_text: str | None,
        tool_schemas: list[dict[str, Any]],
        tool_dispatcher: Any,
        floor: str = "fast",
        ceiling: str = "powerful",
        purpose: str = "",
        on_escalate: Any = None,
        build_tool_schemas: Any = None,
        wrapup_deadline_fn: Any = None,
        **kwargs: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Escalating agentic tool loop with FULL mid-loop escalation.

        Each attempt runs the whole ReAct loop on one tier. If the model returns
        the ESCALATE sentinel as its final answer (even after running tools), the
        draft is DISCARDED and the loop is re-run fresh on the next tier up. A
        tier whose provider can't run tools is skipped upward. ``on_escalate`` (an
        async callback) lets the caller reset turn-scoped state — e.g. the
        tool-outcome ledger — between attempts so a discarded attempt's tool
        failures don't poison the next tier's give-up floor.

        ``build_tool_schemas`` (optional ``(provider) -> list`` — may be async) is
        called once per attempt to REBUILD the tool schemas for that tier's provider
        protocol + context window (a fast and a powerful tier can speak different
        wire protocols and have different windows). When ``None`` the passed-in
        ``tool_schemas`` are reused unchanged (back-compat for existing callers).

        ``wrapup_deadline_fn`` (optional ``() -> float``) recomputes the residual
        wrap-up budget FRESH per attempt from the live BudgetGovernor, so a late
        tier is bounded by the time actually left — not a value frozen at entry.
        When set it overrides any ``wrapup_deadline_s`` in ``kwargs`` for the call.

        ``can_escalate`` (``idx < last tier``) is passed into each
        ``provider.complete_with_tools`` so a provider that persistently leaks an
        unparsed tool call returns the ESCALATE sentinel (re-run on a stronger tier)
        instead of leaking raw text or flooring at a non-final tier.
        """
        tiers = tier_span(floor, ceiling)
        final_text, calls = "", []  # type: tuple[str, list[dict[str, Any]]]
        retried_tiers: set[str] = set()  # ADR-2 — one same-tier retry per tier per call
        for idx, tier in enumerate(tiers):
            provider, model, degraded = self._registry.resolve_tier_with_fallback(tier)
            can_escalate = idx < len(tiers) - 1 and self._can_escalate_meaningfully(
                tiers, idx, provider, model
            )
            if idx < len(tiers) - 1 and not can_escalate:
                log.engine.info(
                    "[llm_gateway] tools: every stronger tier resolves to the SAME "
                    "provider and model — running as if at the ceiling so this "
                    "attempt is DELIVERED instead of discarded and re-run",
                    extra={"_fields": {
                        "purpose": purpose, "tier": tier,
                        "provider": getattr(provider, "name", ""), "model": model,
                        "tiers_above": list(tiers[idx + 1:]),
                    }},
                )
            # Rebuild schemas for THIS tier's provider (protocol + window differ per
            # tier); reuse the passed-in list when no builder is supplied.
            if build_tool_schemas is not None:
                schemas = build_tool_schemas(provider)
                if inspect.isawaitable(schemas):
                    schemas = await schemas
            else:
                schemas = tool_schemas
            if schemas and not provider.supports_tools and can_escalate:
                # Can't run the loop on this tier — climb to a tool-capable one.
                log.engine.info(
                    "[llm_gateway] tools: tier not tool-capable — stepping up",
                    extra={"_fields": {"purpose": purpose, "skip_tier": tier, "model": model}},
                )
                continue
            sys = _augment_system(system_text, can_escalate)
            attempt_kwargs = dict(kwargs)
            if wrapup_deadline_fn is not None:
                attempt_kwargs["wrapup_deadline_s"] = wrapup_deadline_fn()
            try:
                final_text, calls = await provider.complete_with_tools(
                    user_text=user_text, system_text=sys, tool_schemas=schemas,
                    tool_dispatcher=tool_dispatcher, can_escalate=can_escalate,
                    model=model, **attempt_kwargs,
                )
            except BaseException as exc:
                # ADR-2 — same-tier retry-once on a TRANSIENT fault, via the RecoveryActuator,
                # BEFORE cascading (flagged). A recovered retry sets (final_text, calls) and
                # falls through to the escalate-check; if the retry also fails (or the flag is
                # off), the original cascade (with on_escalate reset) / re-raise logic runs.
                if (
                    is_cascadable_fault(exc)
                    and _retry_same_tier_enabled()
                    and tier not in retried_tiers
                ):
                    retried_tiers.add(tier)
                    retried = await self._retry_same_tier_once(
                        partial(
                            provider.complete_with_tools,
                            user_text=user_text, system_text=sys, tool_schemas=schemas,
                            tool_dispatcher=tool_dispatcher, can_escalate=can_escalate,
                            model=model, **attempt_kwargs,
                        ),
                        tier, purpose,
                    )
                else:
                    retried = None
                if retried is not None:
                    final_text, calls = retried
                else:
                    # F-16/F-17: a CLASSIFIED provider fault on this tier cascades to the
                    # next tier up instead of dead-ending at the user. Reset turn-scoped
                    # state (the tool-outcome ledger) via on_escalate exactly like a
                    # discarded ESCALATE attempt so the failed attempt doesn't poison the
                    # recovery tier's give-up floor. Non-fault or LAST tier re-raises.
                    if not (can_escalate and is_cascadable_fault(exc)):
                        # F-19 — an unrecoverable fault outcome must be explainable.
                        if is_cascadable_fault(exc):
                            log.engine.warning(
                                "[llm_gateway] tools: provider fault not recoverable — re-raising",
                                extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                                   "model": model,
                                                   "exc_type": type(exc).__name__,
                                                   "degraded_from": degraded,
                                                   "at_ceiling": not can_escalate}},
                            )
                        raise
                    log.engine.warning(
                        "[llm_gateway] tools: tier failed — falling back",
                        extra={"_fields": {"purpose": purpose, "from_tier": tier,
                                           "to_tier": tiers[idx + 1], "model": model,
                                           "exc_type": type(exc).__name__,
                                           "degraded_from": degraded}},
                    )
                    retry_ledger.record_retry(
                        kind="tier_escalation", provider=tier,
                        detail=f"{tier}->{tiers[idx + 1]}:{type(exc).__name__}",
                    )
                    if on_escalate is not None:
                        await on_escalate(tier, tiers[idx + 1])
                    continue
            if can_escalate and is_escalate_signal(final_text):
                # `can_escalate` now guarantees a DIFFERENT target above (ESC-22), so
                # reaching here means the step up can genuinely produce another class
                # of answer. The "same target" branch that used to live here is gone
                # rather than left as a guard: it is unreachable by construction, and
                # an unreachable guard reads to the next person like a live one.
                log.engine.info(
                    "[llm_gateway] tools: model escalated mid-loop — discard + step up",
                    extra={"_fields": {"purpose": purpose, "from_tier": tier, "model": model,
                                       "to_tier": tiers[idx + 1], "tool_calls": len(calls)}},
                )
                if on_escalate is not None:
                    await on_escalate(tier, tiers[idx + 1])
                continue
            return final_text, calls
        return final_text, calls


def _augment_system(system_text: str | None, can_escalate: bool) -> str | None:
    """Append the escalation instruction to a system prompt (when escalation is
    still possible). Returns the input unchanged at the ceiling."""
    if not can_escalate:
        return system_text
    return (system_text or "") + ESCALATE_INSTRUCTION


def _augment_messages(messages: list[Message], can_escalate: bool) -> list[Message]:
    """Return a NEW message list with the escalation instruction folded into the
    system message (or a new leading system message). Input is never mutated."""
    if not can_escalate:
        return messages
    out: list[Message] = []
    injected = False
    for m in messages:
        if not injected and m.role == "system":
            out.append(Message(role="system", content=m.content + ESCALATE_INSTRUCTION))
            injected = True
        else:
            out.append(m)
    if not injected:
        out.insert(0, Message(role="system", content=ESCALATE_INSTRUCTION.strip()))
    return out
