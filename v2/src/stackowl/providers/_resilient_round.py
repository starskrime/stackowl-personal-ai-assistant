"""SP-2/SP-3 — the ONE shared per-round resilience site for every provider.

Every remote round (each ``create()``/``generate_content()`` in the tool loop, the
wrap-up round, the ``complete()``/``stream()`` round) is wrapped in
:func:`resilient_round`, so the circuit-breaker gate + half-open single-probe
admission + rate-limiter token + classified fault recording all live at ONE
audited site instead of three scatterings.

Design (per the converged C2 spec, CONFLICT 1/2/3):

* **Read/write split is the spine.** Selection (``registry.resolve_tier_with_fallback``)
  READS ``breaker.state`` — pure, sync, byte-identical happy path. Calling WRITES
  breaker state HERE — per-round, classified, locked, fail-open.
* The breaker records a fault ONLY for a **classified upstream fault**
  (:func:`is_provider_fault`) — never a user-stop / budget-kill / our-own-bug,
  which would feed the breaker a false signal (CONFLICT 1).
* The breaker gate runs FIRST (no I/O); the limiter ``acquire`` runs AFTER it
  (CONFLICT/F117 ordering: never burn a token / a back-pressure wait on a call
  that is about to short-circuit OPEN).
* Fail-open everywhere ≠ fail-silent: a breaker/limiter INTERNAL error never
  breaks a round that would otherwise succeed — it is logged and the round
  proceeds. A real provider fault always RE-RAISES (no swallow).
* The half-open in-flight flag is released in a ``finally`` (even on
  ``CancelledError``) so a failed/cancelled probe never wedges the breaker OPEN.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from stackowl.exceptions import (
    BudgetBreach,
    CircuitOpenError,
    DurableReplayUncertain,
    ProviderError,
    ResumeTranscriptError,
    TurnStopped,
)
from stackowl.infra.observability import log
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState
from stackowl.providers.rate_limiter import RateLimiter

# The control-flow signals + our-own-bug errors that must NEVER count as a
# provider fault (CONFLICT 1 / SP-3). Keyed on EXCEPTION TYPE — structural, no
# English-keyword matching. ``CancelledError`` (a BaseException) is handled
# explicitly in ``is_provider_fault`` since it is not an ``Exception`` subclass.
_NOT_PROVIDER_FAULTS: tuple[type[BaseException], ...] = (
    TurnStopped,            # cooperative user-stop — not a provider failure
    BudgetBreach,           # budget-kill — not a provider failure
    DurableReplayUncertain,  # durable replay park — our orchestration, not upstream
    ResumeTranscriptError,  # malformed resume transcript — OUR bug, not upstream
)


def is_provider_fault(exc: BaseException) -> bool:
    """Classify whether ``exc`` is a real upstream provider fault (SP-3).

    Structural classification (exception type / status code) — NO English-message
    matching. Counts toward the breaker ONLY genuine upstream faults; never our
    bugs, never user/budget control-flow signals.

    Fault (record ok=False): SDK transport errors (``APIError``/``APIConnectionError``/
    ``APITimeoutError`` for anthropic/openai/gemini), a 5xx/429 status, a raw
    connection/timeout error. A :class:`ProviderError` wrapping such a cause is a
    fault UNLESS its cause is an empty-choices ``ValueError`` (a protocol oddity,
    not an upstream outage) or one of the non-fault control-flow signals.

    NOT a fault: ``TurnStopped`` / ``BudgetBreach`` / ``DurableReplayUncertain`` /
    ``ResumeTranscriptError`` / ``CancelledError`` / a malformed-args parse
    ``ValueError`` / an empty-choices ``ProviderError``.
    """
    import asyncio

    # Control-flow + our-own-bug signals never count, even if wrapped.
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, _NOT_PROVIDER_FAULTS):
        return False

    # A ProviderError wraps an underlying cause — classify by the cause so an
    # empty-choices ValueError (protocol oddity) is NOT a fault but a wrapped SDK
    # APIError IS.
    if isinstance(exc, ProviderError):
        cause = exc.cause
        if isinstance(cause, (*_NOT_PROVIDER_FAULTS, asyncio.CancelledError)):
            return False
        if isinstance(cause, ValueError):
            # empty-choices / parse oddity surfaced as ProviderError — not an outage.
            return False
        return _is_transport_error(cause)

    # A bare ValueError (malformed args / parse) is our handling, not an outage.
    if isinstance(exc, ValueError):
        return False

    return _is_transport_error(exc)


def _is_transport_error(exc: BaseException) -> bool:
    """True when ``exc`` is an SDK/HTTP transport fault (5xx/429/connection/timeout).

    Probes the three SDK error hierarchies by import (lazy — providers may not all
    be installed) plus a structural ``status_code`` check (5xx/429) and the stdlib
    connection/timeout types. Unknown exception types default to NOT a fault so an
    unexpected internal bug never silently trips the breaker.
    """
    # stdlib transport faults
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True

    # Structural HTTP status check (works across SDKs that expose ``status_code``).
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
        return True

    # SDK error hierarchies — lazy-imported so a missing optional dep never crashes.
    try:
        import openai

        if isinstance(exc, (openai.APIError, openai.APIConnectionError, openai.APITimeoutError)):
            # A 4xx other than 429 is a request error (our payload), not an outage;
            # the 429 + 5xx + status-less connection/timeout cases ARE outages.
            return not _is_request_error_4xx(status)
    except Exception:  # noqa: BLE001 — optional dep / import race; never crash classification
        pass

    try:
        import anthropic

        if isinstance(exc, anthropic.APIError):
            return not _is_request_error_4xx(status)
    except Exception:  # noqa: BLE001
        pass

    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            return not _is_request_error_4xx(code)
    except Exception:  # noqa: BLE001
        pass

    return False


def _is_request_error_4xx(status: object) -> bool:
    """True for a non-429 4xx status (a request error / our payload, NOT an outage).

    A status-less error (connection/timeout) returns False so it is treated as an
    outage by the callers; 429 is excluded (it IS an outage / back-pressure).
    """
    return isinstance(status, int) and 400 <= status <= 499 and status != 429


async def resilient_round[T](
    breaker: CircuitBreaker | None,
    limiter: RateLimiter | None,
    do_round: Callable[[], Awaitable[T]],
    *,
    is_provider_fault: Callable[[BaseException], bool] = is_provider_fault,
) -> T:
    """Run ONE remote round under breaker + limiter protection (SP-2).

    Order (CONFLICT/F117): (1) cheap breaker gate FIRST — OPEN (and not an
    admitted half-open probe) raises :class:`CircuitOpenError` before any I/O;
    (2) ``limiter.acquire()`` AFTER the gate; (3) execute ``do_round`` and record
    a CLASSIFIED outcome onto the breaker, re-raising ALWAYS.

    When ``breaker``/``limiter`` are ``None`` (nothing configured) this is a
    byte-identical pass-through. Breaker/limiter INTERNAL errors fail OPEN (logged,
    round proceeds); a real provider fault is recorded then re-raised.
    """
    provider = breaker._provider_name if breaker is not None else "?"
    log.engine.debug(
        "[resilient_round] entry",
        extra={"_fields": {"provider": provider, "has_breaker": breaker is not None}},
    )

    # 1. Breaker gate FIRST (no I/O). A half-open breaker admits exactly one probe.
    admitted_probe = False
    if breaker is not None:
        try:
            state = breaker.state  # sync, cheap
            if state is CircuitState.OPEN:
                log.engine.info(
                    "[resilient_round] decision — short-circuit (breaker OPEN)",
                    extra={
                        "_fields": {
                            "provider": provider,
                            "retry_after_seconds": breaker.retry_after_seconds,
                        }
                    },
                )
                raise CircuitOpenError(provider, breaker.retry_after_seconds)
            if state is CircuitState.HALF_OPEN:
                admitted_probe = await breaker.admit_probe()
                if not admitted_probe:
                    # Another caller is the in-flight probe — treat as OPEN, do NOT call.
                    log.engine.info(
                        "[resilient_round] decision — half-open probe already in flight, "
                        "skipping (treated as OPEN)",
                        extra={"_fields": {"provider": provider}},
                    )
                    raise CircuitOpenError(provider, breaker.retry_after_seconds)
        except CircuitOpenError:
            raise
        except Exception as exc:  # B5 fail-open — a breaker internal error must not break a good round.
            log.engine.error(
                "[resilient_round] breaker gate raised — failing open (proceeding)",
                exc_info=exc,
                extra={"_fields": {"provider": provider}},
            )

    # 2. Limiter AFTER the gate (never burn a token on a short-circuited call).
    if limiter is not None:
        try:
            await limiter.acquire()
        except Exception as exc:  # B5 fail-open — limiter internal error must not break a good round.
            log.engine.error(
                "[resilient_round] limiter.acquire raised — failing open (proceeding)",
                exc_info=exc,
                extra={"_fields": {"provider": provider}},
            )

    # 3. Execute the round; record a CLASSIFIED outcome; re-raise ALWAYS.
    try:
        result = await do_round()
    except BaseException as exc:
        recorded = False
        if breaker is not None and is_provider_fault(exc):
            try:
                await breaker.record(ok=False)
                recorded = True
            except Exception as rec_exc:  # B5 — recording must never mask the real error.
                log.engine.error(
                    "[resilient_round] breaker.record(ok=False) raised — continuing to re-raise",
                    exc_info=rec_exc,
                    extra={"_fields": {"provider": provider}},
                )
        log.engine.debug(
            "[resilient_round] exit — round raised",
            extra={
                "_fields": {
                    "provider": provider,
                    "classified_fault": recorded,
                    "exc_type": type(exc).__name__,
                }
            },
        )
        raise
    else:
        if breaker is not None:
            await breaker.record(ok=True)
        log.engine.debug(
            "[resilient_round] exit — round ok",
            extra={"_fields": {"provider": provider}},
        )
        return result
    finally:
        # Wedge guard (SP-2): if THIS caller claimed the half-open probe, ensure the
        # in-flight flag is released even if the round was cancelled/raised BEFORE
        # ``record`` ran. ``record``/``clear_probe`` are idempotent on the flag.
        if breaker is not None and admitted_probe:
            breaker.clear_probe()
