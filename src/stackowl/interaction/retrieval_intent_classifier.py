"""RetrievalIntentClassifier — LLM verdict: does this request need a live lookup?

Arc B / PBC — the no-URL sibling of the grounding gate (``grounding_gate.py``).
That gate only fires when the draft carries http(s) URLs; it does nothing for
the far more common shape of overclaim: a confident, un-cited answer to a
live/current question, produced entirely from the model's own (stale or
hallucinated) knowledge, with no URL to inspect and no ``web_search``/
``web_fetch`` call. This classifier supplies the missing judgment — did the
ORIGINAL REQUEST require a live lookup at all? — so ``overclaim_gate`` can
floor the turn when the answer says LOOKUP but no retrieval tool ran.

**LLM classification, not keyword heuristics.** The platform is multilingual
([[feedback_no_hardcoded_english]]) and "does this need research" is not a
string-matchable property ([[feedback_no_hardcoded_keyword_lists]]), so we
never scan the user's request text for words like "news" or "latest". The LLM
makes the semantic call; we only parse the MODEL's own one-word verdict
(``LOOKUP`` / ``KNOWN``) — a token WE control via the prompt.

**Fast tier, one-token verdict.** Mirrors :class:`ClarifyIntentClassifier`'s
shape exactly: lazy ``get_by_tier("fast")`` resolution, ``asyncio.wait_for``
bounded by ``timeout_s`` (default 10.0s — this runs on the pre-deliver hot
path), small ``max_tokens``.

**Fail-safe -> ``False`` (KNOWN) on every degraded path.** Flooring replaces
the WHOLE draft, so a wrong ``True`` erases a legitimate knowledge answer —
the EXPENSIVE direction here. So an unresolvable/no fast provider, a timeout,
a provider error, an empty request, or an ambiguous/unparseable verdict (both
tokens or neither) all fail-safe to ``False``. ``True`` is returned ONLY on an
unambiguous ``LOOKUP`` verdict. Never raises. Every fallback is logged.

Provenance: BUILD (new single-purpose classifier per task-PBC design; kept
separate from ``ClarifyIntentClassifier`` — a different concern, mid-turn
message routing — matching the project's one-classifier-per-concern pattern).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.infra.observability import log, traced_span
from stackowl.providers.base import Message, ModelProvider

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

# Cap the request text shipped to the classifier so a pathological input never
# bloats the one-token call.
_MAX_REQUEST_CHARS = 400
# Truncation budget for LOGGED text (sensitive-data + log-size hygiene).
_LOG_TEXT_CHARS = 80
# One-token verdict. The provider call passes ``disable_thinking=True`` so a
# reasoning fast tier skips its <think> block and emits the verdict token directly —
# without that, a 4-token cap truncated mid-thought and the trigger never fired.
_MAX_TOKENS = 4

# Conservative binary verdict: LOOKUP only when a correct answer clearly DEPENDS
# on information that changes over time or is external to the model's training —
# never a soft "is this research-y" framing. Everything answerable from general,
# stable knowledge or reasoning is KNOWN. Unsure -> KNOWN (the model's own
# instruction mirrors the classifier's own fail-safe direction).
_SYSTEM_PROMPT = (
    "You decide whether correctly answering a request REQUIRES looking up "
    "external or current information the assistant cannot reliably know from "
    "training (live data: news, prices, weather, current events, today's "
    "status, a specific document's contents). If the request can be answered "
    "from general, stable knowledge or reasoning (math, code, definitions, "
    "advice, opinions), answer KNOWN. Be conservative: answer LOOKUP only when "
    "a correct answer clearly DEPENDS on information that changes over time or "
    "is external. If unsure, answer KNOWN. Reply with exactly one word: LOOKUP "
    "or KNOWN."
)


class RetrievalIntentClassifier:
    """LLM-backed verdict: does ``request`` require a live external lookup?

    Constructed once with the :class:`ProviderRegistry`; the fast-tier provider
    is resolved lazily per call so a registry with no provider degrades to the
    fail-safe default rather than failing at construction. Called lazily from
    ``overclaim_gate``'s async wrapper, pre-deliver — never inline on any hot
    receive loop — but still timeout-bounded defensively.
    """

    def __init__(self, provider_registry: ProviderRegistry, *, timeout_s: float = 10.0) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def requires_lookup(self, *, request: str) -> bool:
        """Return ``True`` only on a HIGH-CONFIDENCE ``LOOKUP`` verdict (else ``False``).

        ``True`` means the request's answer depends on live/current/external
        information the model cannot reliably have. Fail-safe -> ``False`` on
        ANY error, missing/unresolvable fast provider, timeout, ambiguous/
        unparseable verdict, or empty ``request``. Never raises.
        """
        r_len = len(request)
        # 1. ENTRY
        log.engine.debug(
            "retrieval_intent_classifier.requires_lookup: entry",
            extra={"_fields": {"request_len": r_len}},
        )

        if not request.strip():
            log.engine.info(
                "retrieval_intent_classifier.requires_lookup: empty request — fail-safe to known",
                extra={"_fields": {"requires_lookup": False}},
            )
            return False

        resolved = self._resolve_provider()
        if resolved is None:
            log.engine.warning(
                "retrieval_intent_classifier.requires_lookup: no fast provider — fail-safe to known",
                extra={"_fields": {"requires_lookup": False}},
            )
            return False
        provider, model = resolved

        try:
            user_text = self._build_user_text(request)
            # Bounded call: a hung fast provider must never stall the pre-deliver
            # gate. CancelledError (not an Exception subclass) still propagates.
            async with traced_span(log.engine, "retrieval_intent_classifier.requires_lookup.provider_call"):
                result = await asyncio.wait_for(
                    provider.complete(
                        [
                            Message(role="system", content=_SYSTEM_PROMPT),
                            Message(role="user", content=user_text),
                        ],
                        model=model,
                        max_tokens=_MAX_TOKENS,
                        disable_thinking=True,
                    ),
                    timeout=self._timeout_s,
                )
            verdict = (result.content or "").strip()
        except TimeoutError:  # hung provider — fail-safe rather than stall delivery
            log.engine.warning(
                "retrieval_intent_classifier.requires_lookup: provider call timed out — fail-safe to known",
                extra={"_fields": {"requires_lookup": False, "timeout_s": self._timeout_s}},
            )
            return False
        except Exception as exc:  # self-healing — a verdict call must never raise
            log.engine.error(
                "retrieval_intent_classifier.requires_lookup: provider call failed — fail-safe to known",
                exc_info=exc,
                extra={"_fields": {"requires_lookup": False}},
            )
            return False

        verdict_bool = self._parse_verdict(verdict)
        # 2. DECISION — the raw verdict and the parsed bool (truncated text).
        log.engine.info(
            "retrieval_intent_classifier.requires_lookup: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "requires_lookup": verdict_bool,
                }
            },
        )
        # 4. EXIT
        return verdict_bool

    # ------------------------------------------------------------------ helpers

    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        """Resolve the fast-tier (provider, model), or ``None`` on any registry error."""
        try:
            return self._registry.get_by_tier_and_model("fast")
        except Exception as exc:  # self-healing — missing provider must not raise
            log.engine.warning(
                "retrieval_intent_classifier._resolve_provider: get_by_tier failed",
                exc_info=exc,
            )
            return None

    @staticmethod
    def _build_user_text(request: str) -> str:
        """Render the (capped) classification prompt body."""
        r = request[:_MAX_REQUEST_CHARS]
        return "\n".join([f"REQUEST: {r}", "Reply LOOKUP or KNOWN."])

    @staticmethod
    def _parse_verdict(verdict: str) -> bool:
        """Map the model's one-word verdict to a bool (fail-safe -> ``False``/KNOWN).

        Case- and token-order robust, parsing only the MODEL's controlled token
        (never the user's multilingual request text):

        * ``lookup`` present and ``known`` absent -> ``True`` (high-confidence LOOKUP).
        * ``known`` present and ``lookup`` absent -> ``False`` (KNOWN).
        * BOTH or NEITHER present (empty / ambiguous / garbage) -> the fail-safe
          default ``False`` — the cheap/safe direction (a wrong ``True`` erases a
          legitimate answer, so any doubt collapses to KNOWN). Logged.
        """
        low = verdict.lower().lstrip()
        has_lookup = "lookup" in low
        has_known = "known" in low
        if has_lookup and not has_known:
            return True
        if has_known and not has_lookup:
            return False
        log.engine.warning(
            "retrieval_intent_classifier._parse_verdict: ambiguous verdict — fail-safe to known",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "has_lookup": has_lookup,
                    "has_known": has_known,
                }
            },
        )
        return False
