"""ScheduleCommitClassifier — LLM verdict: does this draft PROMISE future scheduled work?

Overclaim trigger 4 — the no-tool-call sibling of ``RetrievalIntentClassifier``
(trigger 3). That classifier judges the REQUEST ("did this need a live lookup");
this one judges the RESPONSE ("does this draft commit to doing something for the
user LATER, on a schedule, without the user asking again"). A confident "Sure,
I'll ping you in 5 minutes!" or "I'll check every 2 hours and let you know" is an
overclaim exactly like a fabricated citation when NO ``schedules``-effect tool
(``cronjob`` create/watch) ran this turn — the promise is text only, nothing was
actually scheduled.

**LLM classification, not keyword heuristics.** The platform is multilingual
([[feedback_no_hardcoded_english]]) and "does this promise a schedule" is not a
string-matchable property ([[feedback_no_hardcoded_keyword_lists]]), so we never
scan the draft for words like "remind" or "ping". The LLM makes the semantic
call; we only parse the MODEL's own one-word verdict (``COMMIT`` / ``NONE``) — a
token WE control via the prompt.

**Fast tier, one-token verdict.** Mirrors :class:`RetrievalIntentClassifier`'s
shape exactly: lazy ``get_by_tier("fast")`` resolution, ``asyncio.wait_for``
bounded by ``timeout_s`` (default 10.0s — pre-deliver hot path), small
``max_tokens``.

**Fail-safe -> ``False`` (NONE) on every degraded path.** Flooring replaces the
WHOLE draft, so a wrong ``True`` erases a legitimate answer — the EXPENSIVE
direction here. An unresolvable/no fast provider, a timeout, a provider error,
an empty draft, or an ambiguous/unparseable verdict all fail-safe to ``False``.
``True`` is returned ONLY on an unambiguous ``COMMIT`` verdict. Never raises.
Every fallback is logged.

Provenance: BUILD (new single-purpose classifier, trigger 4 of ``overclaim_gate``
— kept separate from ``RetrievalIntentClassifier``, a different concern judging
the request rather than the draft, matching the project's one-classifier-per-
concern pattern).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from stackowl.infra.observability import log, traced_span
from stackowl.providers.base import Message, ModelProvider

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

# Cap the response text shipped to the classifier so a pathological draft never
# bloats the one-token call.
_MAX_RESPONSE_CHARS = 400
# Truncation budget for LOGGED text (sensitive-data + log-size hygiene).
_LOG_TEXT_CHARS = 80
# One-token verdict. The provider call passes ``disable_thinking=True`` so a
# reasoning fast tier skips its <think> block and emits the verdict token directly —
# without that, a 4-token cap truncated mid-thought and the trigger never fired.
_MAX_TOKENS = 4

_SYSTEM_PROMPT = (
    "You decide whether the ASSISTANT's reply commits to doing something for "
    "the user LATER, at a future time or on a repeating schedule (e.g. it "
    "promises to ping, remind, check in on, notify, alert, or monitor "
    "something and report back) — a promise whose fulfillment REQUIRES the "
    "assistant to act again on its own, without the user asking again. Reply "
    "COMMIT ONLY if the reply is making that promise FOR THE FIRST TIME, right "
    "now, in this reply. Reply NONE if the reply only answers now, asks a "
    "question, describes what it CAN do in general, requires the user to ask "
    "again themselves, or REPORTS ON / LISTS scheduled work that already "
    "exists (e.g. a status summary of currently-scheduled tasks, confirming "
    "something is 'already scheduled' or 'active') — describing an existing "
    "schedule is not a new promise, even though it uses schedule-related "
    "words. Be conservative: if unsure, answer NONE. Reply with exactly one "
    "word: COMMIT or NONE."
)


class ScheduleCommitClassifier:
    """LLM-backed verdict: does ``response`` promise future scheduled work?

    Constructed once with the :class:`ProviderRegistry`; the fast-tier provider
    is resolved lazily per call so a registry with no provider degrades to the
    fail-safe default rather than failing at construction. Called lazily from
    ``overclaim_gate``'s async wrapper, pre-deliver — never inline on any hot
    receive loop — but still timeout-bounded defensively.
    """

    def __init__(self, provider_registry: ProviderRegistry, *, timeout_s: float = 10.0) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def commits_to_future_schedule(self, *, response: str) -> bool:
        """Return ``True`` only on a HIGH-CONFIDENCE ``COMMIT`` verdict (else ``False``).

        ``True`` means the draft promises future scheduled work the assistant
        must perform unprompted. Fail-safe -> ``False`` on ANY error, missing/
        unresolvable fast provider, timeout, ambiguous/unparseable verdict, or
        empty ``response``. Never raises.
        """
        r_len = len(response)
        # 1. ENTRY
        log.engine.debug(
            "schedule_commit_classifier.commits_to_future_schedule: entry",
            extra={"_fields": {"response_len": r_len}},
        )

        if not response.strip():
            log.engine.info(
                "schedule_commit_classifier.commits_to_future_schedule: empty response — fail-safe to none",
                extra={"_fields": {"commits": False}},
            )
            return False

        resolved = self._resolve_provider()
        if resolved is None:
            log.engine.warning(
                "schedule_commit_classifier.commits_to_future_schedule: no fast provider — fail-safe to none",
                extra={"_fields": {"commits": False}},
            )
            return False
        provider, model = resolved

        try:
            user_text = self._build_user_text(response)
            # Bounded call: a hung fast provider must never stall the pre-deliver
            # gate. CancelledError (not an Exception subclass) still propagates.
            async with traced_span(
                log.engine, "schedule_commit_classifier.commits_to_future_schedule.provider_call"
            ):
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
                "schedule_commit_classifier.commits_to_future_schedule: provider call timed out — fail-safe to none",
                extra={"_fields": {"commits": False, "timeout_s": self._timeout_s}},
            )
            return False
        except Exception as exc:  # self-healing — a verdict call must never raise
            log.engine.error(
                "schedule_commit_classifier.commits_to_future_schedule: provider call failed — fail-safe to none",
                exc_info=exc,
                extra={"_fields": {"commits": False}},
            )
            return False

        verdict_bool = self._parse_verdict(verdict)
        # 2. DECISION — the raw verdict, the parsed bool, and a truncated snippet
        # of the draft that was actually judged. Added 2026-07-22 after a live
        # false-positive (COMMIT on a draft that did not actually promise future
        # scheduled work) could not be root-caused from logs alone: a floored
        # turn's draft is deliberately never persisted (persist_turn drops it),
        # and this call previously logged only response_len — no way to see
        # WHAT text tripped the verdict. response_snippet is already bounded by
        # _MAX_RESPONSE_CHARS before it ever reaches here, so this adds no new
        # unbounded-text exposure versus what the classifier itself received.
        log.engine.info(
            "schedule_commit_classifier.commits_to_future_schedule: verdict parsed",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "commits": verdict_bool,
                    "response_snippet": response[:_MAX_RESPONSE_CHARS],
                }
            },
        )
        # 4. EXIT
        return verdict_bool

    # ------------------------------------------------------------------ helpers

    def _resolve_provider(self) -> tuple[ModelProvider, str] | None:
        """Resolve the fast-tier (provider, model), or ``None`` on any registry error."""
        try:
            return self._registry.get_by_tier("fast")
        except Exception as exc:  # self-healing — missing provider must not raise
            log.engine.warning(
                "schedule_commit_classifier._resolve_provider: get_by_tier failed",
                exc_info=exc,
            )
            return None

    @staticmethod
    def _build_user_text(response: str) -> str:
        """Render the (capped) classification prompt body."""
        r = response[:_MAX_RESPONSE_CHARS]
        return "\n".join([f"REPLY: {r}", "Reply COMMIT or NONE."])

    @staticmethod
    def _parse_verdict(verdict: str) -> bool:
        """Map the model's one-word verdict to a bool (fail-safe -> ``False``/NONE).

        Case- and token-order robust, parsing only the MODEL's controlled token
        (never the draft's multilingual text):

        * ``commit`` present and ``none`` absent -> ``True`` (high-confidence COMMIT).
        * ``none`` present and ``commit`` absent -> ``False`` (NONE).
        * BOTH or NEITHER present (empty / ambiguous / garbage) -> the fail-safe
          default ``False`` — the cheap/safe direction (a wrong ``True`` erases a
          legitimate answer, so any doubt collapses to NONE). Logged.
        """
        low = verdict.lower().lstrip()
        has_commit = "commit" in low
        has_none = "none" in low
        if has_commit and not has_none:
            return True
        if has_none and not has_commit:
            return False
        log.engine.warning(
            "schedule_commit_classifier._parse_verdict: ambiguous verdict — fail-safe to none",
            extra={
                "_fields": {
                    "raw_verdict": verdict[:_LOG_TEXT_CHARS],
                    "has_commit": has_commit,
                    "has_none": has_none,
                }
            },
        )
        return False
