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

import enum
import math
from collections.abc import Awaitable, Callable

from stackowl.exceptions import (
    BudgetBreach,
    CircuitOpenError,
    DurableReplayUncertain,
    ProviderError,
    RateLimitError,
    ResumeTranscriptError,
    TurnStopped,
)
from stackowl.infra import retry_ledger
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


class FailureCause(enum.Enum):
    """FX-01 — WHY a round failed, not just whether it counts as a breaker fault.

    ``is_provider_fault`` collapses this to a bool for the breaker; callers that
    want to react differently per cause (route around a dead credential, ease
    off pacing on a 429, back off harder on a real outage) use
    :func:`classify_failure_cause` directly instead of re-deriving it.
    """

    NOT_A_FAULT = "not_a_fault"  # control-flow / our-bug / malformed args
    AUTH = "auth"                # 401/403 or an unrecognised 4xx — bad credential/request
    RATE_LIMIT = "rate_limit"    # 429 — back-pressure, not an outage
    SERVER_5XX = "server_5xx"
    TRANSPORT = "transport"      # connection/timeout, or a status-less SDK transport error
    # D02.6 — causes split OUT of the old catch-all 4xx -> AUTH. Each exists only
    # because it unlocks a DIFFERENT recovery action; a taxonomy that does not
    # change what we do is decoration. Reached by STATUS CODE, never by matching
    # English in an error message.
    BILLING = "billing"                    # 402 — credit exhausted; rotate, do not retry
    MODEL_NOT_FOUND = "model_not_found"    # 404 — bad model id; fall back to another model
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413 — compress and retry, never blind-retry
    TIMEOUT = "timeout"                    # 408 — rebuild the client and retry
    OVERLOADED = "overloaded"              # 529 — provider saturated; back off harder than a 5xx
    # 400 — OUR payload is malformed. Split out of AUTH because the two send an
    # operator to opposite places: measured live, a background miner has been
    # taking 400s for weeks under a log line reading "credential failure", which
    # points at the API key for what is a request-construction bug.
    BAD_REQUEST = "bad_request"


class RecoveryAction(enum.Enum):
    """What to DO about a cause — the reason the taxonomy above earns its keep.

    Ported from the reference platform's failover taxonomy, which is genuinely
    hard-won incident knowledge. NOT ported: its ``_classify_by_message`` path
    (English substring matching) and the three of its members named after specific
    model runtimes — those names belong in the design doc, not here. Both are
    against standing rules, and every reason they encode is reachable from a status
    code or a ``ProviderConfig.quirks`` entry instead: same coverage, and a
    provider-specific fact stays configuration rather than a branch in ``src/``.
    See ``docs/reference-mapping/designs/D02.6.md`` for the member-by-member mapping.
    """

    NONE = "none"                  # not a fault — do nothing
    RETRY = "retry"                # transient; a plain retry can self-heal
    BACKOFF = "backoff"            # retry, but slower — saturation
    ROTATE_CREDENTIAL = "rotate"   # this credential is done; try another
    COMPRESS = "compress"          # payload too big; shrink before retrying
    FALLBACK_MODEL = "fallback_model"  # this model is unusable; try another
    ABORT = "abort"                # nothing here self-heals; surface honestly


#: Cause -> action. Kept as data rather than an if-tree so a new cause cannot be
#: added without someone stating what to DO about it.
RECOVERY_FOR_CAUSE: dict[FailureCause, RecoveryAction] = {
    FailureCause.NOT_A_FAULT: RecoveryAction.NONE,
    FailureCause.TRANSPORT: RecoveryAction.RETRY,
    FailureCause.SERVER_5XX: RecoveryAction.RETRY,
    FailureCause.TIMEOUT: RecoveryAction.RETRY,
    FailureCause.RATE_LIMIT: RecoveryAction.BACKOFF,
    FailureCause.OVERLOADED: RecoveryAction.BACKOFF,
    FailureCause.BILLING: RecoveryAction.ROTATE_CREDENTIAL,
    FailureCause.PAYLOAD_TOO_LARGE: RecoveryAction.COMPRESS,
    FailureCause.MODEL_NOT_FOUND: RecoveryAction.FALLBACK_MODEL,
    # A malformed request is OUR bug. Another model would reject it too, and
    # cascading would only spend the turn's budget hiding it — abort and surface.
    FailureCause.BAD_REQUEST: RecoveryAction.ABORT,
    # An auth failure is the one we ALREADY log as "blind retry will not
    # self-heal this" — now the taxonomy says so rather than a log line.
    FailureCause.AUTH: RecoveryAction.ABORT,
}


#: A cause counts against the circuit breaker iff its prescribed action is to try
#: the same provider again (RETRY/BACKOFF) — i.e. we believe the provider itself is
#: unhealthy. DERIVED rather than listed, so the truth table cannot drift from the
#: action map above the way it silently would if a new cause were added to one and
#: not the other.
_BREAKER_FAULTS: frozenset[FailureCause] = frozenset(
    cause for cause, action in RECOVERY_FOR_CAUSE.items()
    if action in (RecoveryAction.RETRY, RecoveryAction.BACKOFF)
)


def recovery_for(exc: BaseException) -> RecoveryAction:
    """The action to take for ``exc``. Unknown causes ABORT (fail-safe: surface
    honestly rather than retry something we do not understand)."""
    return RECOVERY_FOR_CAUSE.get(classify_failure_cause(exc), RecoveryAction.ABORT)


def is_provider_fault(exc: BaseException) -> bool:
    """Classify whether ``exc`` is a real upstream provider fault (SP-3).

    Thin boolean view over :func:`classify_failure_cause` (RATE_LIMIT/SERVER_5XX/
    TRANSPORT all count; AUTH and NOT_A_FAULT don't) — kept as the stable public
    name the breaker write-seam calls.

    NOT a fault: ``TurnStopped`` / ``BudgetBreach`` / ``DurableReplayUncertain`` /
    ``ResumeTranscriptError`` / ``CancelledError`` / a malformed-args parse
    ``ValueError`` / an empty-choices ``ProviderError`` / an auth-shaped 4xx / a
    billing, model-not-found or payload-too-large 4xx (none of which the provider
    can heal by being called again).

    D02.6 changed ONE input to this: a **408** used to reach here via the 4xx
    catch-all as AUTH (not a fault). It is now TIMEOUT, and counts — the same way a
    status-less ``TimeoutError`` always has. Treating a timeout as a credential
    problem was never intended; it fell out of collapsing the whole 4xx range.
    """
    return classify_failure_cause(exc) in _BREAKER_FAULTS


def classify_failure_cause(exc: BaseException) -> FailureCause:
    """Structural classification (exception type / status code) — NO English-message
    matching. Same control-flow tree ``is_provider_fault`` always used; now names
    the specific cause instead of collapsing straight to a bool.
    """
    import asyncio

    # Control-flow + our-own-bug signals never count, even if wrapped.
    if isinstance(exc, asyncio.CancelledError):
        return FailureCause.NOT_A_FAULT
    if isinstance(exc, _NOT_PROVIDER_FAULTS):
        return FailureCause.NOT_A_FAULT

    # A ProviderError wraps an underlying cause — classify by the cause so an
    # empty-choices ValueError (protocol oddity) is NOT a fault but a wrapped SDK
    # APIError IS.
    if isinstance(exc, ProviderError):
        cause = exc.cause
        if isinstance(cause, (*_NOT_PROVIDER_FAULTS, asyncio.CancelledError)):
            return FailureCause.NOT_A_FAULT
        if isinstance(cause, ValueError):
            # empty-choices / parse oddity surfaced as ProviderError — not an outage.
            return FailureCause.NOT_A_FAULT
        return _classify_transport_cause(cause)

    # A bare ValueError (malformed args / parse) is our handling, not an outage.
    if isinstance(exc, ValueError):
        return FailureCause.NOT_A_FAULT

    return _classify_transport_cause(exc)


def _cause_for_status(status: object) -> FailureCause:
    """Map an HTTP-ish status to a cause. Status-less (connection/timeout SDK
    errors) falls through to TRANSPORT — matches the pre-FX-01 behavior of
    treating a status-less SDK transport error as an outage, not our payload.
    """
    if isinstance(status, int):
        if status == 429:
            return FailureCause.RATE_LIMIT
        # 529 is a real status some providers return for saturation. Checked
        # BEFORE the 5xx range so it backs off harder than a generic 500.
        if status == 529:
            return FailureCause.OVERLOADED
        if 500 <= status <= 599:
            return FailureCause.SERVER_5XX
        # D02.6 — specific 4xx codes BEFORE the catch-all. Collapsing all of
        # 400-499 to AUTH is why a 413 payload-too-large was blind-retried: the
        # retry cannot self-heal a request that is simply too big.
        if status == 402:
            return FailureCause.BILLING
        if status == 404:
            return FailureCause.MODEL_NOT_FOUND
        if status == 408:
            return FailureCause.TIMEOUT
        if status == 413:
            return FailureCause.PAYLOAD_TOO_LARGE
        if status == 400:
            return FailureCause.BAD_REQUEST
        if 400 <= status <= 499:
            # Unrecognised 4xx stays AUTH — fail-safe, unchanged behaviour.
            return FailureCause.AUTH
    return FailureCause.TRANSPORT


def _classify_transport_cause(exc: BaseException) -> FailureCause:
    """SDK/HTTP transport classification (5xx/429/connection/timeout/4xx-auth).

    Probes the three SDK error hierarchies by import (lazy — providers may not all
    be installed) plus a structural ``status_code`` check. Unknown exception types
    default to NOT_A_FAULT so an unexpected internal bug never silently trips the
    breaker.
    """
    # stdlib transport faults
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return FailureCause.TRANSPORT

    # Structural HTTP status check (works across SDKs that expose ``status_code``).
    #
    # D02.6 — this gate used to admit only 429 and 5xx, so a 402/404/413 raised by
    # anything outside the three probed SDK hierarchies below fell through to
    # NOT_A_FAULT and got RecoveryAction.NONE. The comment said "works across SDKs";
    # the condition said otherwise. Widened to every error status.
    #
    # Deliberately floored at 400: a status-less error and a non-error status must
    # NOT reach ``_cause_for_status``, whose status-less fallthrough is TRANSPORT —
    # which IS a breaker fault. Widening past this line would start tripping the
    # circuit breaker on exceptions that merely happen to carry a status attribute.
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 400:
        return _cause_for_status(status)

    # SDK error hierarchies — lazy-imported so a missing optional dep never crashes.
    try:
        import openai

        if isinstance(exc, (openai.APIError, openai.APIConnectionError, openai.APITimeoutError)):
            return _cause_for_status(status)
    except Exception:  # noqa: BLE001 — optional dep / import race; never crash classification
        pass

    try:
        import anthropic

        if isinstance(exc, anthropic.APIError):
            return _cause_for_status(status)
    except Exception:  # noqa: BLE001
        pass

    try:
        from google.genai import errors as genai_errors

        if isinstance(exc, genai_errors.APIError):
            code = getattr(exc, "code", None)
            return _cause_for_status(code)
    except Exception:  # noqa: BLE001
        pass

    return FailureCause.NOT_A_FAULT


def _is_transport_error(exc: BaseException) -> bool:
    """True when ``exc`` is an SDK/HTTP transport fault (5xx/429/connection/timeout).

    Kept as the stable name ``anthropic_provider.py``/``gemini_provider.py`` import;
    re-implemented over :func:`_classify_transport_cause` so the truth table can't
    drift from ``classify_failure_cause``'s.
    """
    return _classify_transport_cause(exc) in _BREAKER_FAULTS


def _parse_retry_after_seconds(exc: BaseException) -> float | None:
    """Best-effort: read a numeric Retry-After header off exc.response, if present.

    Defensive by construction — ANY failure (missing attrs, non-numeric value,
    HTTP-date form) falls back to None so a parsing bug can never crash a round.

    Also rejects non-finite values (``inf``/``nan``): ``float()`` happily
    accepts the literal strings "inf", "Infinity", and "nan", and either one
    reaching :meth:`CircuitBreaker.open_for` would wedge the breaker OPEN
    forever with no self-healing path (see Finding 1, Task 7+8 review) —
    treated the same as any other unparseable header so the caller falls
    through to the ``cooldown_hours`` fallback / generic threshold path.
    """
    raw: object = None
    try:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
        if raw is None:
            return None
        parsed = float(raw)
        if not math.isfinite(parsed):
            log.engine.debug(
                "[resilient_round] retry-after header non-finite — falling through",
                extra={"_fields": {"raw": str(raw)[:40]}},
            )
            return None
        return parsed
    except Exception as exc_parse:  # noqa: BLE001 — parsing is best-effort, never fatal.
        log.engine.debug(
            "[resilient_round] retry-after header unparseable — falling through",
            extra={"_fields": {"raw": str(raw)[:40] if raw is not None else None, "error": str(exc_parse)}},
        )
        return None


async def resilient_round[T](
    breaker: CircuitBreaker | None,
    limiter: RateLimiter | None,
    do_round: Callable[[], Awaitable[T]],
    *,
    is_provider_fault: Callable[[BaseException], bool] = is_provider_fault,
    cooldown_hours: float | None = None,
) -> T:
    """Run ONE remote round under breaker + limiter protection (SP-2).

    Order (CONFLICT/F117): (1) cheap breaker gate FIRST — OPEN (and not an
    admitted half-open probe) raises :class:`CircuitOpenError` before any I/O;
    (2) ``limiter.acquire()`` AFTER the gate; (3) execute ``do_round`` and record
    a CLASSIFIED outcome onto the breaker, re-raising ALWAYS; on a RATE_LIMIT
    fault this also opens the breaker for a quota-aware duration — the
    response's own reset signal if parseable, else ``cooldown_hours``, else no
    change (generic threshold path).

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
                retry_ledger.record_retry(
                    kind="circuit_open_skip", provider=provider, detail="OPEN",
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
                    retry_ledger.record_retry(
                        kind="circuit_open_skip", provider=provider,
                        detail="half_open_probe_in_flight",
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
        except RateLimitError:
            # A DELIBERATE cap refusal (F124, fail-closed): a zero-refill bucket
            # cannot recover, so the cap must be honored — NOT failed open. Propagate
            # so the caller backs off rather than over-running a real rate limit.
            raise
        except Exception as exc:  # B5 fail-open — limiter INTERNAL error must not break a good round.
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
        # FX-01 — cause-aware side effects, independent of the (possibly injected,
        # e.g. test-only) ``is_provider_fault`` used for the breaker above. Both are
        # best-effort: an error here must never mask the real exception being raised.
        cause = classify_failure_cause(exc)
        if cause is FailureCause.AUTH:
            log.engine.error(
                "[resilient_round] provider credential/permission failure — "
                "blind retry will not self-heal this",
                extra={"_fields": {"provider": provider, "exc_type": type(exc).__name__}},
            )
        elif cause is FailureCause.BAD_REQUEST:
            # Detail matters here in a way it does not for a credential failure:
            # the caller built a request this provider rejects, and the provider's
            # own words are the only clue to WHICH part. Truncated to 200 chars,
            # matching the SQL-logging rule — a rejection can echo the payload back.
            log.engine.error(
                "[resilient_round] provider rejected the REQUEST (400) — our payload, "
                "not our credentials; a blind retry sends the same bad request again",
                extra={"_fields": {
                    "provider": provider,
                    "exc_type": type(exc).__name__,
                    "detail": str(exc)[:200],
                }},
            )
        elif cause is FailureCause.RATE_LIMIT and limiter is not None:
            try:
                limiter.penalize()
            except Exception as pen_exc:  # B5 — must never mask the real error.
                log.engine.error(
                    "[resilient_round] limiter.penalize raised — continuing to re-raise",
                    exc_info=pen_exc,
                    extra={"_fields": {"provider": provider}},
                )
            else:
                retry_ledger.record_retry(kind="rate_limit_penalty", provider=provider)
            if breaker is not None:
                reset_seconds = _parse_retry_after_seconds(exc)
                cooldown_seconds = (
                    reset_seconds if reset_seconds is not None
                    else (cooldown_hours * 3600.0 if cooldown_hours is not None else None)
                )
                if cooldown_seconds is not None:
                    try:
                        await breaker.open_for(cooldown_seconds)
                    except Exception as cd_exc:  # B5 — must never mask the real error.
                        log.engine.error(
                            "[resilient_round] breaker.open_for raised — continuing to re-raise",
                            exc_info=cd_exc,
                            extra={"_fields": {"provider": provider}},
                        )
                    else:
                        retry_ledger.record_retry(
                            kind="cooldown", provider=provider,
                            detail=f"{cooldown_seconds:.0f}s",
                        )
        log.engine.debug(
            "[resilient_round] exit — round raised",
            extra={
                "_fields": {
                    "provider": provider,
                    "classified_fault": recorded,
                    "cause": cause.value,
                    # What the taxonomy says to DO — so a live log line answers
                    # "why did (or didn't) this recover?" without reading the map.
                    "recovery": RECOVERY_FOR_CAUSE.get(cause, RecoveryAction.ABORT).value,
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
