"""ScheduleCommitClassifier — LLM verdict: does this draft PROMISE future scheduled work?

Overclaim trigger 4 — the no-tool-call sibling of :class:`RetrievalIntentClassifier`
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

**Fast tier, one-token verdict, on the shared base (2026-07-22).** Uses
:mod:`stackowl.interaction.classifier_base`'s Pieces A/B/C — pinned
``get_by_tier("fast")`` resolution, a bounded ``asyncio.wait_for`` call, and the
shared two-token verdict parser. This migration is a pure refactor: same
prompt, same token budget, same timeout, same fail-safe direction, verified
against the exact pre-migration behavior (including the two real
false-positive drafts captured as regression fixtures in this module's test
file after the live incidents this classifier was involved in — see
``tests/interaction/test_schedule_commit_classifier.py``).

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

from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.interaction.classifier_base import (
    parse_two_token_verdict,
    resolve_fixed_tier,
    safe_complete,
)
from stackowl.providers.base import Message

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
    "You decide whether the ASSISTANT's reply commits to doing something BRAND "
    "NEW for the user LATER, at a future time or on a repeating schedule — a "
    "promise it is making FOR THE FIRST TIME, right now, in this reply, that "
    "was NOT already set up before this reply (e.g. it newly promises to ping, "
    "remind, check in on, notify, alert, or monitor something and report back, "
    "with nothing actually scheduled yet). Reply COMMIT only for that NEW, "
    "first-time promise. Reply NONE if the reply only answers now, asks a "
    "question, describes what it CAN do in general, requires the user to ask "
    "again themselves, or is about scheduled/automated work that ALREADY "
    "EXISTS — this covers BOTH a status summary/list of currently-scheduled "
    "tasks AND a closing remark that already-configured automation will "
    "keep going (e.g. 'I'll keep the scheduled agents running', 'staying on "
    "standby', 'have a great day'). Restating or reassuring about EXISTING, "
    "already-configured automation is NOT a new promise, even when phrased "
    "with future-tense words like 'I'll keep' or 'I'll stay' — those words "
    "describe continuity of something already set up, not a new commitment. "
    "Be conservative: if unsure, answer NONE. Reply with exactly one word: "
    "COMMIT or NONE."
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

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.engine, call_name="schedule_commit_classifier",
        )
        if resolved is None:
            log.engine.warning(
                "schedule_commit_classifier.commits_to_future_schedule: no fast provider — fail-safe to none",
                extra={"_fields": {"commits": False}},
            )
            return False
        provider, model = resolved

        user_text = self._build_user_text(response)
        outcome = await safe_complete(
            provider, model,
            [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=user_text),
            ],
            max_tokens=_MAX_TOKENS,
            timeout_s=self._timeout_s,
            logger=log.engine,
            call_name="schedule_commit_classifier",
        )
        if outcome.result is None:  # timeout or provider error — safe_complete already logged
            return False
        verdict = (outcome.result.content or "").strip()

        verdict_bool, confident = parse_two_token_verdict(
            verdict, true_token="commit", false_token="none",
            ambiguous_default=False, use_leading_token_tiebreak=False,
        )
        if not confident:
            log.engine.warning(
                "schedule_commit_classifier._parse_verdict: ambiguous verdict — fail-safe to none",
                extra={"_fields": {"raw_verdict": verdict[:_LOG_TEXT_CHARS]}},
            )
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

    @staticmethod
    def _build_user_text(response: str) -> str:
        """Render the (capped) classification prompt body."""
        r = response[:_MAX_RESPONSE_CHARS]
        return "\n".join([f"REPLY: {r}", "Reply COMMIT or NONE."])
