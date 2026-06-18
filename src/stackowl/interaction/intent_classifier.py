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

**Fast tier.** The verdict is a cheap, one-token call, so the classifier resolves the
FAST-tier provider lazily (``get_by_tier("fast")``) at call time — a missing provider
degrades gracefully (fail-safe) instead of failing at construction.

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

import asyncio
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.providers.base import Message, ModelProvider

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

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
    therefore bounded by ``timeout_s`` (default 3s — a one-token classification must
    be fast; if it isn't, fail safe rather than stall the loop).
    """

    def __init__(
        self, provider_registry: ProviderRegistry, *, timeout_s: float = 3.0,
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
            return True

        provider = self._resolve_provider()
        if provider is None:
            log.gateway.warning(
                "intent_classifier.is_answer: no fast provider — fail-safe to answer",
                extra={"_fields": {"classified": True}},
            )
            return True

        try:
            user_text = self._build_user_text(question, choices, message)
            # Bound the inline call: a hung fast provider must not HOL-block the
            # single receive loop. asyncio.CancelledError propagates (it is not an
            # Exception subclass) so a cancelled receive task still tears down cleanly.
            result = await asyncio.wait_for(
                provider.complete(
                    [
                        Message(role="system", content=_SYSTEM_PROMPT),
                        Message(role="user", content=user_text),
                    ],
                    model="",
                    max_tokens=4,
                ),
                timeout=self._timeout_s,
            )
            verdict = (result.content or "").strip()
        except TimeoutError:  # hung provider — fail-safe rather than stall
            log.gateway.warning(
                "intent_classifier.is_answer: provider call timed out — fail-safe to answer",
                extra={
                    "_fields": {"classified": True, "timeout_s": self._timeout_s}
                },
            )
            return True
        except Exception as exc:  # self-healing — a verdict call must never raise
            log.gateway.error(
                "intent_classifier.is_answer: provider call failed — fail-safe to answer",
                exc_info=exc,
                extra={"_fields": {"classified": True}},
            )
            return True

        classified = self._parse_verdict(verdict)
        # 2. DECISION — the raw verdict and the parsed bool (truncated text).
        log.gateway.info(
            "intent_classifier.is_answer: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "classified": classified,
                }
            },
        )
        # 4. EXIT
        return classified

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

        provider = self._resolve_provider()
        if provider is None:
            log.gateway.warning(
                "intent_classifier.is_steer: no fast provider — fail-safe to new",
                extra={"_fields": {"steer": False}},
            )
            return False

        try:
            user_text = self._build_steer_user_text(running_ask, message)
            # Bound the inline call: a hung fast provider must not HOL-block the
            # single receive loop. CancelledError (not an Exception subclass)
            # still propagates so a cancelled receive task tears down cleanly.
            result = await asyncio.wait_for(
                provider.complete(
                    [
                        Message(role="system", content=_STEER_SYSTEM_PROMPT),
                        Message(role="user", content=user_text),
                    ],
                    model="",
                    max_tokens=4,
                ),
                timeout=self._timeout_s,
            )
            verdict = (result.content or "").strip()
        except TimeoutError:  # hung provider — fail-safe to NEW rather than stall
            log.gateway.warning(
                "intent_classifier.is_steer: provider call timed out — fail-safe to new",
                extra={"_fields": {"steer": False, "timeout_s": self._timeout_s}},
            )
            return False
        except Exception as exc:  # self-healing — a verdict call must never raise
            log.gateway.error(
                "intent_classifier.is_steer: provider call failed — fail-safe to new",
                exc_info=exc,
                extra={"_fields": {"steer": False}},
            )
            return False

        steer = self._parse_steer_verdict(verdict)
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

        provider = self._resolve_provider()
        if provider is None:
            log.gateway.warning(
                "intent_classifier.is_steer_incoherent: no fast provider — fail-safe to veto",
                extra={"_fields": {"veto": True}},
            )
            return True

        try:
            user_text = self._build_coherence_user_text(running_ask, message)
            # Bound the inline call: a hung fast provider must not HOL-block the
            # single receive loop. CancelledError (not an Exception subclass)
            # still propagates so a cancelled receive task tears down cleanly.
            result = await asyncio.wait_for(
                provider.complete(
                    [
                        Message(role="system", content=_COHERENCE_SYSTEM_PROMPT),
                        Message(role="user", content=user_text),
                    ],
                    model="",
                    max_tokens=4,
                ),
                timeout=self._timeout_s,
            )
            verdict = (result.content or "").strip()
        except TimeoutError:  # hung provider — fail-safe to VETO rather than stall
            log.gateway.warning(
                "intent_classifier.is_steer_incoherent: provider call timed out — fail-safe to veto",
                extra={"_fields": {"veto": True, "timeout_s": self._timeout_s}},
            )
            return True
        except Exception as exc:  # self-healing — a verdict call must never raise
            log.gateway.error(
                "intent_classifier.is_steer_incoherent: provider call failed — fail-safe to veto",
                exc_info=exc,
                extra={"_fields": {"veto": True}},
            )
            return True

        veto = self._parse_coherence_verdict(verdict)
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

    def _resolve_provider(self) -> ModelProvider | None:
        """Resolve the fast-tier provider, or ``None`` on any registry error.

        Lazy + defensive: ``get_by_tier`` raising (no providers at all) or any other
        registry failure degrades to ``None`` so :meth:`is_answer` fail-safes.
        """
        try:
            return self._registry.get_by_tier("fast")
        except Exception as exc:  # self-healing — missing provider must not raise
            log.gateway.warning(
                "intent_classifier._resolve_provider: get_by_tier failed",
                exc_info=exc,
            )
            return None

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
    def _parse_verdict(verdict: str) -> bool:
        """Map the model's one-word verdict to a bool (fail-safe → ``True``).

        Case-insensitive and token-order robust. A verbose verdict can contain BOTH
        tokens (e.g. "NEW — this does not answer the question"); naive precedence on
        ``answer`` would misclassify that as an answer and silently revert the feature.
        So we test for each token independently:

        * ``new`` present and ``answer`` absent → ``False`` (a NEW request).
        * ``answer`` present and ``new`` absent → ``True`` (an answer).
        * BOTH present or NEITHER (empty, ambiguous, garbage like "maybe") → the
          fail-safe default ``True`` — the safe choice that never drops a genuine
          answer — with a debug log noting the ambiguous verdict.

        This parses only the MODEL's controlled token, never the user's (multilingual)
        message.

        When BOTH tokens appear, the LEADING token wins — a verdict that opens with
        ``NEW`` ("NEW — this does not answer the question") is a NEW pivot even though
        "answer" trails inside the justification, and must not be swallowed. Only a
        both-present verdict with NEITHER as the clear leader (e.g. "answer or new?")
        falls through to the fail-safe.
        """
        low = verdict.lower().lstrip()
        has_answer = "answer" in low
        has_new = "new" in low
        if has_new and not has_answer:
            return False
        if has_answer and not has_new:
            return True
        if has_answer and has_new:
            # BOTH present: defer to whichever token leads the verdict.
            if low.startswith("new"):
                return False
            if low.startswith("answer"):
                return True
        log.gateway.warning(
            "intent_classifier._parse_verdict: ambiguous verdict — fail-safe to answer",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "has_answer": has_answer,
                    "has_new": has_new,
                }
            },
        )
        return True

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
    def _parse_steer_verdict(verdict: str) -> bool:
        """Map the model's one-word verdict to a bool (fail-safe → ``False``/NEW).

        The CONSERVATIVE mirror of :meth:`_parse_verdict`: STEER is the EXPENSIVE
        direction, so it is granted ONLY on an unambiguous STEER verdict. Case- and
        token-order robust, parsing only the MODEL's controlled token (never the
        user's multilingual message):

        * ``steer`` present and ``new`` absent → ``True`` (a high-confidence STEER).
        * ``new`` present and ``steer`` absent → ``False`` (a NEW request).
        * BOTH present → ``False`` (NEW). The asymmetry is deliberate: a both-token
          verdict ("steer or new?", "NEW — do not steer") is NOT unambiguous enough
          to grant the expensive STEER, so it collapses to the cheap, safe NEW
          direction. (Contrast :meth:`_parse_verdict`, whose fail-safe is the other
          way, so it lets a leading token break a both-present tie.)
        * NEITHER present (empty / ambiguous / garbage like "maybe") → the fail-safe
          default ``False`` (NEW) — the cheap, visible direction. Logged.
        """
        low = verdict.lower().lstrip()
        has_steer = "steer" in low
        has_new = "new" in low
        if has_steer and not has_new:
            return True
        if has_new and not has_steer:
            return False
        # BOTH or NEITHER present: never unambiguous enough for the expensive
        # STEER → fail-safe to NEW (the conservative, cheap direction).
        log.gateway.warning(
            "intent_classifier._parse_steer_verdict: ambiguous verdict — fail-safe to new",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "has_steer": has_steer,
                    "has_new": has_new,
                }
            },
        )
        return False

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

    @staticmethod
    def _parse_coherence_verdict(verdict: str) -> bool:
        """Map the one-word coherence verdict to a VETO bool (fail-safe → ``True``).

        Returns ``True`` to VETO (incoherent/contradictory → NEW) and ``False`` to
        allow the steer (coherent refinement). The fail-safe is the SAFE VETO
        direction (opposite of :meth:`_parse_steer_verdict`'s cheap-NEW fail-safe,
        but the same SPIRIT — both default to a separate, recoverable answer). Case-
        and token-order robust, parsing only the MODEL's controlled token (never the
        user's multilingual message):

        * ``conflict`` present and ``refine`` absent → ``True`` (veto).
        * ``refine`` present and ``conflict`` absent → ``False`` (allow the steer).
        * BOTH present → the LEADING token decides; a tie with no clear leader falls
          through to the fail-safe.
        * NEITHER present (empty / ambiguous / garbage like "maybe") → the fail-safe
          default ``True`` (VETO) — the safe direction. Logged.
        """
        low = verdict.lower().lstrip()
        has_refine = "refine" in low
        has_conflict = "conflict" in low
        if has_conflict and not has_refine:
            return True
        if has_refine and not has_conflict:
            return False
        if has_refine and has_conflict:
            # BOTH present: defer to whichever token leads the verdict.
            if low.startswith("conflict"):
                return True
            if low.startswith("refine"):
                return False
        log.gateway.warning(
            "intent_classifier._parse_coherence_verdict: ambiguous verdict — fail-safe to veto",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "has_refine": has_refine,
                    "has_conflict": has_conflict,
                }
            },
        )
        return True
