"""Shared primitives for StackOwl's fast-tier LLM classifier/judge call sites.

At least 10 independent classifier/judge call sites in this codebase
(``schedule_commit_classifier.py``, ``retrieval_intent_classifier.py``,
``intent_classifier.py``'s three verdicts, ``feedback_classifier.py``,
``retry_intent_classifier.py``, ``owls/router.py``, ``owls/evolution.py``,
``memory/critic_scorer_handler.py``, ``pipeline/acceptance_llm.py``,
``pipeline/delivery_gate.py``'s apology generator) each hand-roll the
identical skeleton: resolve a provider for a tier, make a bounded call with
``disable_thinking``, parse the model's verdict, fail safe on any error. The
docstrings admit this outright — several say "mirrors X's shape exactly."

**The root cause this closes**: a reasoning-capable fast-tier model burns its
ENTIRE output-token budget on invisible ``<think>``/``reasoning_content``
tokens before ever emitting the actual verdict, unless ``disable_thinking``
is set AND the token budget is sized for a no-thinking response. This exact
bug was independently rediscovered and patched in ``owls/router.py``,
``feedback_classifier.py``, ``owls/evolution.py``,
``delivery_gate.py``'s apology generator, and ``schedule_commit_classifier.py``
— five incidents, one root cause, never fixed once at a shared layer until
now. See ``docs/structured-output-spike.md`` for the confirmed additional
finding that a JSON-schema constraint does NOT make a classifier immune to
this — ``disable_thinking`` stays mandatory even with a schema.

**Three composable pieces, not one forced interface** — the 10 existing call
sites genuinely split into two provider-resolution strategies (pinned
``get_by_tier`` vs. circuit-aware ``get_with_cascade``) and two output shapes
(one-word verdict vs. JSON verdict). Collapsing these into one interface
would either force an awkward shape onto some callers or silently change
which resolution strategy a given call site uses.

**Deliberately excluded, not migrated**: ``pipeline/persistence.py``'s
``judge_delivery``/``judge_relevance``. That judge has a ``None``-vettable
tri-state (not a fixed boolean fail-safe), a second-provider retry ladder,
and a fail-safe *direction* that depends on whether the turn is
consequential — forcing it onto this base would risk recreating the exact
"a capped judge falsely ruled a genuine delivery a give-up" incident its own
docstring already warns against. It stays fully bespoke.

**Deliberately deferred, not built**: confidence-gated escalation via
``LLMGateway``. In the current deployment ONE provider (NeraAiRaw) serves
fast/standard/powerful identically — escalating would re-invoke the
identical model/weights for zero quality benefit, just added latency.
``LLMGateway``'s own docstring already says meta/classifier calls should pin
``floor == ceiling``. Revisit only once a real multi-model tier deployment
ships.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import traced_span
from stackowl.providers.base import CompletionResult, Message, ModelProvider

if TYPE_CHECKING:  # pragma: no cover — typing-only
    import logging

    from stackowl.providers.registry import ProviderRegistry

__all__ = [
    "resolve_fixed_tier",
    "resolve_cascade_tier",
    "safe_complete",
    "SafeCompleteOutcome",
    "parse_two_token_verdict",
]


@dataclass(frozen=True, slots=True)
class SafeCompleteOutcome:
    """Result of :func:`safe_complete`. ``result`` is ``None`` on ANY failure;
    ``timed_out`` distinguishes a timeout from every other failure mode
    (provider error, no-provider, etc.) for callers that need the distinction
    in their OWN diagnostic reason tag (e.g. ``intent_classifier.py``'s
    ``AnswerVerdict.reason`` — "provider_timeout" vs "provider_error" — an
    audit-log distinction that pre-dates this shared module and must survive
    the migration). Callers that only care about success/failure just check
    ``outcome.result is None``."""

    result: CompletionResult | None
    timed_out: bool = False


# =============================================================================
# Piece A — provider resolution. Two strategies, never collapsed into one:
# pinned (get_by_tier) for hot-path classifiers that must not silently escalate
# tiers mid-decision, and circuit-aware cascade (get_with_cascade) for callers
# that already opt into tier fallback today (owls/router.py, acceptance_llm.py,
# critic_scorer_handler.py, delivery_gate.py's apology generator).
# =============================================================================


def resolve_fixed_tier(
    registry: ProviderRegistry, tier: str, *, logger: logging.Logger, call_name: str,
) -> tuple[ModelProvider, str] | None:
    """Resolve ``tier`` via the registry's pinned ``get_by_tier`` — never raises.

    Mirrors the identical ``_resolve_provider`` helper duplicated across
    ``schedule_commit_classifier.py``, ``retrieval_intent_classifier.py``, and
    ``intent_classifier.py``. Any registry error (missing provider, config
    error) degrades to ``None`` rather than propagating, so the caller's own
    fail-safe default takes over.
    """
    try:
        return registry.get_by_tier(tier)
    except Exception as exc:  # self-healing — missing provider must not raise
        logger.warning(
            f"{call_name}.resolve_fixed_tier: get_by_tier failed",
            exc_info=exc,
            extra={"_fields": {"tier": tier}},
        )
        return None


def resolve_cascade_tier(
    registry: ProviderRegistry, tier: str, *, logger: logging.Logger, call_name: str,
) -> tuple[ModelProvider, str] | None:
    """Resolve ``tier`` via the registry's circuit-aware ``get_with_cascade`` —
    never raises. Walks fast→standard→powerful→local, skipping OPEN-circuit
    providers. ``None`` when every provider is unavailable
    (``AllProvidersUnavailableError``) or any other registry error occurs.
    """
    try:
        return registry.get_with_cascade(tier)
    except Exception as exc:  # self-healing — no available provider must not raise
        logger.warning(
            f"{call_name}.resolve_cascade_tier: get_with_cascade failed",
            exc_info=exc,
            extra={"_fields": {"tier": tier}},
        )
        return None


# =============================================================================
# Piece B — bounded call. The ONE place the disable_thinking/token-budget
# correctness fix lives from now on. timeout_s=None preserves call sites that
# currently have no timeout (owls/router.py, acceptance_llm.py, critic_scorer_
# handler.py, delivery_gate.py's apology generator) — a float wraps the call in
# asyncio.wait_for. Never swallows CancelledError (not an Exception subclass,
# so it already propagates through the except Exception branch below).
# =============================================================================


async def safe_complete(
    provider: ModelProvider,
    model: str,
    messages: list[Message],
    *,
    max_tokens: int,
    timeout_s: float | None,
    logger: logging.Logger,
    call_name: str,
    disable_thinking: bool = True,
    response_format: dict[str, object] | None = None,
) -> SafeCompleteOutcome:
    """Bounded ``provider.complete(...)`` call — never raises.

    ``outcome.result`` is ``None`` on ANY failure; ``outcome.timed_out`` is
    ``True`` only for the timeout path specifically, for the rare caller that
    needs that distinction (most callers just check ``outcome.result is
    None``). ``disable_thinking`` defaults ``True`` (matches all 10 existing
    call sites) but is overridable for a hypothetical future caller that
    genuinely wants a reasoning trace. ``response_format`` is optional
    structured-output support (``docs/structured-output-spike.md`` confirmed
    the deployed gateway honors OpenAI's ``response_format``) — passed
    through as-is; providers that don't understand it ignore it silently (an
    unread kwarg), which is why ``disable_thinking`` remains mandatory
    regardless of whether a schema is supplied.
    """
    logger.debug(
        f"{call_name}.safe_complete: entry",
        extra={"_fields": {"model": model, "max_tokens": max_tokens, "timeout_s": timeout_s}},
    )
    call_kwargs: dict[str, object] = {
        "max_tokens": max_tokens,
        "disable_thinking": disable_thinking,
    }
    if response_format is not None:
        call_kwargs["response_format"] = response_format
    try:
        async with traced_span(logger, f"{call_name}.safe_complete.provider_call"):
            coro = provider.complete(messages, model=model, **call_kwargs)
            if timeout_s is None:
                return SafeCompleteOutcome(result=await coro)
            return SafeCompleteOutcome(result=await asyncio.wait_for(coro, timeout=timeout_s))
    except TimeoutError:  # hung provider — fail-safe rather than stall the caller
        logger.warning(
            f"{call_name}.safe_complete: provider call timed out",
            extra={"_fields": {"timeout_s": timeout_s}},
        )
        return SafeCompleteOutcome(result=None, timed_out=True)
    except Exception as exc:  # self-healing — a bounded call must never raise
        logger.error(
            f"{call_name}.safe_complete: provider call failed",
            exc_info=exc,
        )
        return SafeCompleteOutcome(result=None)


# =============================================================================
# Piece C — one-word verdict parser. Replaces the byte-for-byte-duplicated
# _parse_verdict methods in schedule_commit_classifier.py, retrieval_intent_
# classifier.py, and intent_classifier.py's three verdict parsers — verified
# against each site's exact current truth table (see the three tie-break
# shapes below) so this is a pure refactor, not a behavior change.
# =============================================================================


def parse_two_token_verdict(
    raw: str,
    *,
    true_token: str,
    false_token: str,
    ambiguous_default: bool,
    use_leading_token_tiebreak: bool,
) -> tuple[bool, bool]:
    """Map a model's two-token verdict to ``(value, confident)``.

    Case-insensitive, token-order robust, parsing only the MODEL's controlled
    token (never the multilingual input text it judged). Verified against
    every current one-word-verdict classifier's exact tie-break behavior —
    there are genuinely THREE distinct shapes in this codebase today, not
    one, and this function preserves each:

    * ``true_token`` present, ``false_token`` absent -> ``(True, True)``.
    * ``false_token`` present, ``true_token`` absent -> ``(False, True)``.
    * BOTH present, ``use_leading_token_tiebreak=True`` -> whichever token the
      verdict STARTS WITH wins, confident. Matches
      ``intent_classifier.py``'s ``_parse_verdict`` (is_answer) and
      ``_parse_coherence_verdict`` (is_steer_incoherent) — both explicitly
      "defer to whichever token leads the verdict" on a both-present tie.
    * BOTH present, ``use_leading_token_tiebreak=False`` -> falls straight to
      the ambiguous fallback below, regardless of order. Matches
      ``schedule_commit_classifier.py``, ``retrieval_intent_classifier.py``,
      and ``intent_classifier.py``'s ``_parse_steer_verdict`` (is_steer) —
      the latter is EXPLICITLY documented as the asymmetric case: "never
      unambiguous enough for the expensive STEER, so it collapses to the
      cheap, safe NEW direction" even when a leading token exists.
    * NEITHER present, or a both-present tiebreak with no matching
      ``.startswith`` -> ``(ambiguous_default, False)`` — the fail-safe
      default, marked NOT confident so a caller wanting the F-72
      "explicit assumption" audit trail (``intent_classifier.py``'s
      ``AnswerVerdict``) can surface it rather than silently commit it.

    Callers that only need the bool (the other 4 sites) simply discard the
    second tuple element.
    """
    low = raw.lower().lstrip()
    has_true = true_token in low
    has_false = false_token in low
    if has_true and not has_false:
        return True, True
    if has_false and not has_true:
        return False, True
    if has_true and has_false and use_leading_token_tiebreak:
        if low.startswith(true_token):
            return True, True
        if low.startswith(false_token):
            return False, True
    return ambiguous_default, False
