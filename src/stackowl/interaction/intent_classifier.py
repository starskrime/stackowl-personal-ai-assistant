"""ClarifyIntentClassifier — LLM verdict: does a typed reply ANSWER a pending question?

When a clarify question is pending and the user types a free-text message, the pump
must decide whether that message ANSWERS the parked question (resolve the parked turn)
or is a NEW/UNRELATED request (the pump cancels the clarify and runs a fresh turn).
Today ANY typed message during a pending clarify is swallowed as the answer, so a user
who pivots ("actually, what's the weather?") has their real request erased. This class
adds the missing semantic decision.

**LLM classification, not keyword heuristics.** The platform is multilingual
([[feedback_no_hardcoded_english]]) so we do NOT match English keywords against the
user's MESSAGE. The LLM does the semantic classification; we only parse the MODEL'S
own one-word verdict (``ANSWER`` / ``NEW``) — a token WE control via the prompt — so
verdict-parsing carries no language assumptions about the user's text.

**Fast tier, on the shared base (2026-07-22).** Uses
:mod:`stackowl.interaction.classifier_base`'s Pieces A/B/C — pinned
``get_by_tier("fast")`` resolution, a bounded ``asyncio.wait_for`` call, and the
shared two-token verdict parser. Pure refactor: same prompts, same token
budgets, same timeouts, same fail-safe directions as before this migration.

**Fail-safe → True (treat as an answer).** ANY error, missing provider, ambiguous or
unparseable verdict, or empty message yields ``True``. Rationale: defaulting to
"answer" is no worse than today's always-swallow behaviour, whereas defaulting to
"new" would risk discarding a genuine answer as a fresh turn. Every fallback is logged.

**Generalised for STEER-vs-NEW (Task 15).** :meth:`is_steer` reuses the same
fast-tier, one-token-verdict shape for a mid-turn message's STEER-vs-NEW routing,
but with the OPPOSITE fail-safe direction (→ ``False``/NEW) because there a false
STEER poisons the running turn and loses the new ask invisibly (the expensive
direction), while a false NEW is a cheap, visible second answer.

**Stage-2 coherence veto (concurrent-msg §5.5).** :meth:`is_steer_incoherent`
reuses the same shape AGAIN as the SECOND gate: after ``is_steer`` proposes a
steer (refinement-vs-new), this asks the DISTINCT coherence question — would
FOLDING the message into the running goal blend coherently (REFINE → allow) or
REPLACE/CONTRADICT it (CONFLICT → VETO → NEW)? It fail-safes to ``True`` (VETO),
the SAFE direction: a wrong veto only yields a separate coherent answer, while a
wrong non-veto risks an incoherent old+new blend.

Never raises. Plain class (no Pydantic) — small/OOP per the slice-D operator decision.
Provenance: BUILD (no external agent had a multilingual answer-vs-new-request gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra import decision_ledger
from stackowl.infra.observability import log
from stackowl.interaction.classifier_base import (
    parse_two_token_verdict,
    resolve_fixed_tier,
    safe_complete,
)
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class AnswerVerdict:
    """Explainable result of :meth:`ClarifyIntentClassifier.explain_answer` (F-72).

    ``value`` is the same bool :meth:`ClarifyIntentClassifier.is_answer` returns
    (``True`` = treat the reply as the answer to the pending question). ``confident``
    is ``False`` when ``value`` was reached by a FAIL-SAFE fallback — an
    ambiguous/unparseable verdict, an empty message, a missing provider, a timeout,
    or a provider error — rather than a clear model verdict. F-72 wants those
    low-confidence cases surfaced as an explicit ASSUMPTION (auditable, and a hook a
    caller can use to tell the user "I assumed X — say 'no' to switch") instead of
    silently committed. ``reason`` is a short, non-user-facing diagnostic TAG for the
    audit log (never the user's multilingual text).

    Rendering a localized user-facing hint from a low-confidence verdict, and any
    cross-turn learning from past misclassifications, are DEFERRED (the latter by the
    positive-only-learning directive — we never persist a "got it wrong" record).
    """

    value: bool
    confident: bool
    reason: str

# Cap the text shipped to the classifier so a pathological question/message does not
# bloat the one-token call. A few hundred chars is ample to classify intent.
_MAX_QUESTION_CHARS = 400
_MAX_MESSAGE_CHARS = 400
_MAX_CHOICE_CHARS = 80
_MAX_CHOICES = 12
# Truncation budget for LOGGED text (sensitive-data + log-size hygiene).
_LOG_TEXT_CHARS = 80

_SYSTEM_PROMPT = (
    "You classify whether a user's reply answers a pending question. "
    "A reply ANSWERS the question if it picks one of the offered choices, gives a "
    "free-text answer, confirms or declines (e.g. a short yes/no), or corrects a "
    "prior choice (e.g. 'no, the other one'). It is NEW if it raises an unrelated "
    "request or changes the topic. Reply with exactly one word: ANSWER or NEW."
)

# STEER-vs-NEW verdict (Task 15). Asymmetric cost: a wrong STEER poisons the
# running turn AND loses the new ask invisibly (expensive); a wrong NEW just
# yields a second visible answer (cheap, recoverable). So the prompt is biased
# CONSERVATIVE — STEER is reserved for messages that UNAMBIGUOUSLY refine the
# in-flight task; ANY doubt is NEW. The model emits exactly one controlled token
# (STEER / NEW) which we parse (never the user's multilingual text).
_STEER_SYSTEM_PROMPT = (
    "A task is already running. You decide whether the user's new message is a "
    "STEER (a correction or refinement of the RUNNING task — e.g. 'no, make it "
    "shorter', 'use the other tone') or a NEW request (an unrelated ask or a "
    "topic change). Be conservative: answer STEER ONLY when the message clearly "
    "refines the running task. If you are unsure, or it could be a separate "
    "request, answer NEW. Reply with exactly one word: STEER or NEW."
)

# Stage-2 COHERENCE verdict (concurrent-msg §5.5). This is the running turn's OWN
# coherence check on a message stage-1 already judged a plausible steer. The
# question is DIFFERENT from is_steer's (refinement-vs-new): here the steer is
# assumed plausibly-related, and we ask whether FOLDING it into the in-flight goal
# would blend COHERENTLY (a genuine refinement/addition → REFINE) or whether it
# REPLACES / CONTRADICTS the running goal so folding it would produce an incoherent
# old+new mix (→ CONFLICT, veto to a separate fresh turn). Conservative & SAFE: a
# wrong CONFLICT merely yields a separate coherent answer, while a wrong REFINE
# risks an incoherent blend — so unsure → CONFLICT. The model emits exactly one
# controlled token (REFINE / CONFLICT) which we parse, never the user's text.
_COHERENCE_SYSTEM_PROMPT = (
    "A task is already running and the user sent a follow-up message. Decide "
    "whether folding the follow-up into the RUNNING task stays coherent. Answer "
    "REFINE if the follow-up refines or adds to the running task so they combine "
    "into one coherent goal. Answer CONFLICT if the follow-up would REPLACE or "
    "CONTRADICT the running task, so combining them would be incoherent. If you "
    "are unsure, answer CONFLICT. Reply with exactly one word: REFINE or CONFLICT."
)


class ClarifyIntentClassifier:
    """LLM-backed verdict: does a typed reply answer the pending clarify question?

    Constructed once with the :class:`ProviderRegistry`; the fast-tier provider is
    resolved lazily per call so a registry with no provider degrades to the fail-safe
    default rather than failing at construction.

    ``is_answer`` is awaited inline on the single channel receive loop, so a hung
    fast-tier provider would head-of-line block ALL sessions. The provider call is
    therefore bounded by ``timeout_s`` (default 10s — matches the empirically
    validated fast-tier latency headroom from feedback_classifier's 45s->10s fix;
    3s consistently under-shot real response time and made every call fail-safe).
    """

    def __init__(
        self, provider_registry: ProviderRegistry, *, timeout_s: float = 10.0,
    ) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def is_answer(
        self, *, question: str, choices: tuple[str, ...], message: str,
    ) -> bool:
        """Return ``True`` if ``message`` ANSWERS ``question`` (else a NEW request).

        ``True`` means: resolve the parked clarify turn with ``message`` as the
        answer (a chosen option, a free-text answer, a short confirmation, or a
        correction). ``False`` means: ``message`` is a new/unrelated request — the
        pump should cancel the clarify and run a fresh turn.

        Fail-safe → ``True`` on ANY error, a missing/unresolvable fast provider, an
        ambiguous/unparseable verdict, or an empty ``message``. Never raises.

        Thin delegate over :meth:`explain_answer` (unchanged bool contract for the
        single inline caller); use :meth:`explain_answer` when you also need the
        low-confidence/assumption signal (F-72).
        """
        verdict = await self.explain_answer(
            question=question, choices=choices, message=message,
        )
        return verdict.value

    async def explain_answer(
        self, *, question: str, choices: tuple[str, ...], message: str,
    ) -> AnswerVerdict:
        """Like :meth:`is_answer` but returns an EXPLAINABLE :class:`AnswerVerdict`.

        Thin wrapper over :meth:`_explain_answer` (the verdict logic) that emits one
        ADR-7 ``router`` Decision to the turn ledger — the answer-vs-new direction, the
        diagnostic ``reason`` tag, and the F-72 confidence — so "why did it treat this as
        an answer / a new request?" is a read of the ledger, not a reconstruction. The
        wrapper is the single public boundary, so BOTH the clear-verdict and the
        low-confidence (F-72) paths are captured by the one return. No-op when the ledger
        is unbound (flag off / outside a turn). Never raises.
        """
        verdict = await self._explain_answer(
            question=question, choices=choices, message=message,
        )
        decision_ledger.record_decision(
            point="router",
            verdict="answer" if verdict.value else "new",
            reason=verdict.reason,
            evidence={"confidence": "high" if verdict.confident else "low"},
        )
        return verdict

    async def _explain_answer(
        self, *, question: str, choices: tuple[str, ...], message: str,
    ) -> AnswerVerdict:
        """Classify whether ``message`` answers ``question`` (see :meth:`explain_answer`).

        Carries the same fail-safe bool in ``value`` PLUS ``confident`` (``False``
        when the verdict was a fail-safe fallback) and a diagnostic ``reason`` tag.
        F-72: a low-confidence verdict is logged as an explicit, auditable ASSUMPTION
        — "not silently committed" — and exposed so a caller CAN surface an
        "I assumed X — say 'no' to switch" hint (that user-facing wiring is deferred).
        Never raises.
        """
        q_len = len(question)
        m_len = len(message)
        # 1. ENTRY
        log.gateway.debug(
            "intent_classifier.is_answer: entry",
            extra={
                "_fields": {
                    "question_len": q_len,
                    "message_len": m_len,
                    "n_choices": len(choices),
                }
            },
        )

        # An empty reply carries no intent to classify — fail-safe to answer so the
        # parked turn is not discarded on noise.
        if not message.strip():
            log.gateway.info(
                "intent_classifier.is_answer: empty message — fail-safe to answer",
                extra={"_fields": {"classified": True}},
            )
            return self._low_confidence_answer("empty_message")

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.gateway, call_name="intent_classifier.is_answer",
        )
        if resolved is None:
            log.gateway.warning(
                "intent_classifier.is_answer: no fast provider — fail-safe to answer",
                extra={"_fields": {"classified": True}},
            )
            return self._low_confidence_answer("no_provider")
        provider, model = resolved

        user_text = self._build_user_text(question, choices, message)
        outcome = await safe_complete(
            provider, model,
            [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=user_text),
            ],
            max_tokens=4,
            timeout_s=self._timeout_s,
            logger=log.gateway,
            call_name="intent_classifier.is_answer",
        )
        if outcome.result is None:  # timeout or provider error — safe_complete already logged
            return self._low_confidence_answer(
                "provider_timeout" if outcome.timed_out else "provider_error"
            )
        verdict = (outcome.result.content or "").strip()

        classified, confident = parse_two_token_verdict(
            verdict, true_token="answer", false_token="new",
            ambiguous_default=True, use_leading_token_tiebreak=True,
        )
        # 2. DECISION — the raw verdict and the parsed bool (truncated text).
        log.gateway.info(
            "intent_classifier.is_answer: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "classified": classified,
                    "confident": confident,
                }
            },
        )
        if not confident:
            # An ambiguous/unparseable verdict resolved by the fail-safe default —
            # surface it as an explicit, auditable ASSUMPTION (F-72), not silently.
            return self._low_confidence_answer("ambiguous_verdict", value=classified)
        # 4. EXIT
        return AnswerVerdict(value=classified, confident=True, reason="clear_verdict")

    def _low_confidence_answer(
        self, reason: str, *, value: bool = True,
    ) -> AnswerVerdict:
        """Build + AUDIT-LOG a low-confidence answer assumption (F-72).

        Centralises the "not silently committed" logging for every fail-safe path so
        a low-confidence verdict is always traceable with its assumed direction and
        the reason it was assumed (never the user's multilingual text).
        """
        log.gateway.info(
            "intent_classifier.is_answer: LOW-CONFIDENCE assumption — not silently committed",
            extra={
                "_fields": {
                    "assumed": "answer" if value else "new",
                    "confidence": "low",
                    "reason": reason,
                }
            },
        )
        return AnswerVerdict(value=value, confident=False, reason=reason)

    async def is_steer(self, *, running_ask: str, message: str) -> bool:
        """Return ``True`` only on a HIGH-CONFIDENCE STEER verdict (else NEW).

        Generalises :meth:`is_answer`'s fast-tier, one-token-verdict shape for the
        STEER-vs-NEW decision a mid-turn UNSIGNALED message poses (Task 15): is the
        ``message`` a correction/refinement of the ``running_ask`` (STEER, fold into
        the running turn) or an unrelated request (NEW, run a fresh turn)?

        **Opposite fail-safe direction from** :meth:`is_answer`. ``is_answer``
        fail-safes to ``True`` (answer) because the cheap error there is keeping a
        parked turn. Here the asymmetric cost is REVERSED: a false STEER poisons the
        running turn AND loses the new ask invisibly (expensive), while a false NEW
        is a recoverable, visible second answer (cheap). So ANY error, missing/
        unresolvable fast provider, timeout, ambiguous/unparseable verdict, or empty
        ``message`` yields ``False`` (NEW) — uncertainty defaults to the cheap, safe
        direction. STEER is returned ONLY when the model emits a clear STEER verdict.
        Never raises. Every fallback is logged.
        """
        a_len = len(running_ask)
        m_len = len(message)
        # 1. ENTRY
        log.gateway.debug(
            "intent_classifier.is_steer: entry",
            extra={"_fields": {"running_ask_len": a_len, "message_len": m_len}},
        )

        # An empty message carries no intent — fail-safe to NEW (the cheap
        # direction), so noise never folds onto / poisons the running turn.
        if not message.strip():
            log.gateway.info(
                "intent_classifier.is_steer: empty message — fail-safe to new",
                extra={"_fields": {"steer": False}},
            )
            return False

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.gateway, call_name="intent_classifier.is_steer",
        )
        if resolved is None:
            log.gateway.warning(
                "intent_classifier.is_steer: no fast provider — fail-safe to new",
                extra={"_fields": {"steer": False}},
            )
            return False
        provider, model = resolved

        user_text = self._build_steer_user_text(running_ask, message)
        outcome = await safe_complete(
            provider, model,
            [
                Message(role="system", content=_STEER_SYSTEM_PROMPT),
                Message(role="user", content=user_text),
            ],
            max_tokens=4,
            timeout_s=self._timeout_s,
            logger=log.gateway,
            call_name="intent_classifier.is_steer",
        )
        if outcome.result is None:  # timeout or provider error — safe_complete already logged
            return False
        verdict = (outcome.result.content or "").strip()

        steer, confident = parse_two_token_verdict(
            verdict, true_token="steer", false_token="new",
            ambiguous_default=False, use_leading_token_tiebreak=False,
        )
        if not confident:
            log.gateway.warning(
                "intent_classifier._parse_steer_verdict: ambiguous verdict — fail-safe to new",
                extra={"_fields": {"raw_verdict": verdict[:_LOG_TEXT_CHARS]}},
            )
        # 2. DECISION — the raw verdict and the parsed bool (truncated text).
        log.gateway.info(
            "intent_classifier.is_steer: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "steer": steer,
                }
            },
        )
        # 4. EXIT
        return steer

    async def is_steer_incoherent(self, *, running_ask: str, message: str) -> bool:
        """Stage-2 coherence VETO: would folding ``message`` blend INCOHERENTLY?

        The running turn's OWN coherence judge, the SECOND gate after
        :meth:`is_steer`'s conservative propose stage. ``is_steer`` decides
        refinement-vs-new (would-this-be-a-steer); a CONTRADICTION ("no, I meant Y"
        that flips the goal) can pass that stage yet, folded into the running turn,
        produce an incoherent old+new mix. This method asks the COHERENCE question
        instead: given the ``running_ask`` and the proposed ``message``, return
        ``True`` to VETO (folding would REPLACE/CONTRADICT → incoherent blend → fall
        back to NEW) or ``False`` to allow the steer (a genuine refinement/addition
        → STEER proceeds).

        **Fail-safe → ``True`` (VETO → NEW)** — the SAFE direction. A wrong veto
        only yields a separate coherent answer (cheap, visible); a wrong non-veto
        risks an incoherent blend (expensive, invisible). So ANY error, missing/
        unresolvable fast provider, timeout, ambiguous/unparseable verdict, or empty
        ``message`` yields ``True`` (veto). REFINE (allow the steer) is returned ONLY
        on a clear REFINE verdict. Never raises. Every fallback is logged.

        Mirrors :meth:`is_steer`'s fast-tier, one-token-verdict shape (DRY) but with
        a DISTINCT prompt (coherence/contradiction, not refinement-vs-new) and the
        VETO fail-safe direction.
        """
        a_len = len(running_ask)
        m_len = len(message)
        # 1. ENTRY
        log.gateway.debug(
            "intent_classifier.is_steer_incoherent: entry",
            extra={"_fields": {"running_ask_len": a_len, "message_len": m_len}},
        )

        # An empty message carries no coherent refinement — fail-safe to VETO (the
        # safe direction), so noise is never folded onto the running turn.
        if not message.strip():
            log.gateway.info(
                "intent_classifier.is_steer_incoherent: empty message — fail-safe to veto",
                extra={"_fields": {"veto": True}},
            )
            return True

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.gateway,
            call_name="intent_classifier.is_steer_incoherent",
        )
        if resolved is None:
            log.gateway.warning(
                "intent_classifier.is_steer_incoherent: no fast provider — fail-safe to veto",
                extra={"_fields": {"veto": True}},
            )
            return True
        provider, model = resolved

        user_text = self._build_coherence_user_text(running_ask, message)
        outcome = await safe_complete(
            provider, model,
            [
                Message(role="system", content=_COHERENCE_SYSTEM_PROMPT),
                Message(role="user", content=user_text),
            ],
            max_tokens=4,
            timeout_s=self._timeout_s,
            logger=log.gateway,
            call_name="intent_classifier.is_steer_incoherent",
        )
        if outcome.result is None:  # timeout or provider error — safe_complete already logged
            return True
        verdict = (outcome.result.content or "").strip()

        veto, confident = parse_two_token_verdict(
            verdict, true_token="conflict", false_token="refine",
            ambiguous_default=True, use_leading_token_tiebreak=True,
        )
        if not confident:
            log.gateway.warning(
                "intent_classifier._parse_coherence_verdict: ambiguous verdict — fail-safe to veto",
                extra={"_fields": {"raw_verdict": verdict[:_LOG_TEXT_CHARS]}},
            )
        # 2. DECISION — the raw verdict and the parsed bool (truncated text).
        log.gateway.info(
            "intent_classifier.is_steer_incoherent: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "veto": veto,
                }
            },
        )
        # 4. EXIT
        return veto

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _build_user_text(
        question: str, choices: tuple[str, ...], message: str,
    ) -> str:
        """Render the (capped) classification prompt body.

        Question and message are truncated to a few hundred chars; choices are
        bounded in count and per-choice length so a pathological pending entry can
        never bloat the call.
        """
        q = question[:_MAX_QUESTION_CHARS]
        m = message[:_MAX_MESSAGE_CHARS]
        lines = [f"QUESTION: {q}"]
        if choices:
            rendered = " | ".join(c[:_MAX_CHOICE_CHARS] for c in choices[:_MAX_CHOICES])
            lines.append(f"CHOICES: {rendered}")
        lines.append(f"REPLY: {m}")
        lines.append("Does REPLY answer QUESTION? Reply ANSWER or NEW.")
        return "\n".join(lines)

    @staticmethod
    def _build_steer_user_text(running_ask: str, message: str) -> str:
        """Render the (capped) STEER-vs-NEW classification prompt body.

        Mirrors :meth:`_build_user_text`'s capping — the running ask and the new
        message are each truncated to a few hundred chars so a pathological turn or
        message can never bloat the one-token call.
        """
        a = running_ask[:_MAX_QUESTION_CHARS]
        m = message[:_MAX_MESSAGE_CHARS]
        return "\n".join(
            [
                f"RUNNING TASK: {a}",
                f"NEW MESSAGE: {m}",
                "Is NEW MESSAGE a correction of the RUNNING TASK? "
                "Reply STEER or NEW.",
            ]
        )

    @staticmethod
    def _build_coherence_user_text(running_ask: str, message: str) -> str:
        """Render the (capped) coherence (REFINE-vs-CONFLICT) prompt body.

        Mirrors :meth:`_build_steer_user_text`'s capping — the running ask and the
        follow-up are each truncated to a few hundred chars so a pathological turn
        or message can never bloat the one-token coherence call.
        """
        a = running_ask[:_MAX_QUESTION_CHARS]
        m = message[:_MAX_MESSAGE_CHARS]
        return "\n".join(
            [
                f"RUNNING TASK: {a}",
                f"FOLLOW-UP: {m}",
                "Does folding FOLLOW-UP into RUNNING TASK stay coherent? "
                "Reply REFINE or CONFLICT.",
            ]
        )
