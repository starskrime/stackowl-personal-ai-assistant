"""TurnAchievementWriter — what would COUNT as this turn being done.

EARNED 2026-08-31, live. "Give me in pictures" ran with ``tools_used=false``,
the model replied "I'll draw this as an actual image for you", no image tool was
ever called, and the task closed ``completed``. Bakir's question was the right
one: *why is the task marked completed?*

Because every chat turn was enqueued with a constant:
``achievement="the reply is delivered to the user who asked"``. All three
distinct achievement values across 1,283 live tasks restate delivery, so
completion checked delivery twice and the goal zero times. Deliver a promise and
the goal is "achieved".

The ONE LOOP contract already says a task carries a destination AND an
achievement condition. The destination half was made real in 2026-08-18. This is
the other half.

**WRITTEN FROM THE REQUEST ONLY, BEFORE THE WORK.** ``write()`` takes the
request and nothing else, and the test suite asserts that on the SIGNATURE — a
criterion written after seeing the result can be retrofitted to whatever
happened, which is the failure this replaces. That property is structural here,
not a promise in a docstring.

**THE DEGENERACY GUARD IS OVERLAP, NOT A WORD LIST.** "Force it to name the
observable" cannot be a list of English phrases; the platform is multilingual
([[feedback_no_hardcoded_english]], [[feedback_no_hardcoded_keyword_lists]]). A
criterion that shares no content token with the request is generic BY
CONSTRUCTION — "a reply is delivered to the user" has nothing in common with
"Give me in pictures", in any script. Overlap is derived from the data, so it
works for Azerbaijani as well as English.

**FAIL-SAFE IS TODAY'S CONSTANT.** No provider, timeout, provider error, empty
request, empty or degenerate criterion — all return ``DEFAULT_ACHIEVEMENT``. The
worst case of this module is exactly the behaviour that shipped before it, so it
cannot regress a turn. Never raises.

SHADOW PHASE (Bakir, 2026-08-31): the criterion is written, stored and logged at
INFO. Nothing judges it and no task is reopened yet — enforcement waits on a
measured false-positive rate from real traffic.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.interaction.classifier_base import resolve_fixed_tier, safe_complete
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

#: The pre-2026-08-31 constant. Still the fail-safe: never worse than before.
DEFAULT_ACHIEVEMENT = "the reply is delivered to the user who asked"

#: Unicode word scanner — letters and numbers in ANY script, never [a-z].
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: A token this short carries no content in any language we can assume, and
#: including it would let "a"/"is" fake an overlap. Derived from shape, not from
#: a stopword list.
#:
#: RAISED FROM 3 TO 4 WITH PREFIX MATCHING, because the first version of this
#: guard REFUSED A CORRECT CRITERION: "Give me in pictures" against "an image
#: file is delivered to the user" shares no token — they are synonyms, not the
#: same word. Exact equality is also defeated by inflection alone
#: (pictures/picture). Prefix matching at four characters absorbs ordinary
#: morphology without a stemmer or a language assumption.
_MIN_CONTENT_TOKEN = 4

_MAX_REQUEST_CHARS = 600
_LOG_TEXT_CHARS = 160
_MAX_TOKENS = 60

_SYSTEM_PROMPT = (
    "You state what would COUNT as a request being fulfilled.\n"
    "\n"
    "Given the user's request, reply with ONE short clause naming the OBSERVABLE "
    "thing the user would receive or be able to see. Nothing else — no preamble, "
    "no explanation, no quotes.\n"
    "\n"
    "REUSE THE USER'S OWN WORDS for the thing they asked for, so the criterion "
    "is visibly about THEIR request.\n"
    "\n"
    "Name the thing itself. 'a picture of the tree is delivered to the user'. "
    "'an explanation of BFS for trees with a Python code example'. "
    "'the three cheapest flights, with prices'.\n"
    "\n"
    "NEVER answer with something true of every reply, such as 'a reply is sent' "
    "or 'the user gets an answer'. That describes delivery, not the request.\n"
    "\n"
    "Write it in the same language as the request."
)


def _content_tokens(text: str) -> set[str]:
    """Casefolded Unicode tokens of at least ``_MIN_CONTENT_TOKEN`` characters."""
    return {t.casefold() for t in _WORD_RE.findall(text) if len(t) >= _MIN_CONTENT_TOKEN}


def _references(criterion: str, request: str) -> bool:
    """Does the criterion visibly refer to something in the request?

    Prefix match rather than equality so ordinary inflection (picture/pictures,
    şəkil/şəkli) still counts, without a stemmer or a per-language rule.
    """
    left = _content_tokens(criterion)
    right = _content_tokens(request)
    return any(a.startswith(b) or b.startswith(a) for a in left for b in right)


class TurnAchievementWriter:
    """Produce this turn's achievement condition from the request alone."""

    MAX_CRITERION_CHARS = 200

    def __init__(self, provider_registry: ProviderRegistry, *, timeout_s: float = 10.0) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def write(self, *, request: str) -> str:
        """Return a criterion naming the observable, or ``DEFAULT_ACHIEVEMENT``.

        ``request`` is the ONLY input, deliberately — see the module docstring.
        Never raises; every fallback is logged.
        """
        # 1. ENTRY
        log.engine.debug(
            "turn_achievement_writer.write: entry",
            extra={"_fields": {"request_len": len(request)}},
        )
        if not request.strip():
            log.engine.info(
                "turn_achievement_writer.write: empty request — fail-safe to the constant",
                extra={"_fields": {"achievement": DEFAULT_ACHIEVEMENT}},
            )
            return DEFAULT_ACHIEVEMENT

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.engine, call_name="turn_achievement_writer",
        )
        if resolved is None:
            log.engine.warning(
                "turn_achievement_writer.write: no fast provider — fail-safe to the constant",
                extra={"_fields": {"achievement": DEFAULT_ACHIEVEMENT}},
            )
            return DEFAULT_ACHIEVEMENT
        provider, model = resolved

        outcome = await safe_complete(
            provider, model,
            [
                Message(role="system", content=_SYSTEM_PROMPT),
                Message(role="user", content=request[:_MAX_REQUEST_CHARS]),
            ],
            max_tokens=_MAX_TOKENS,
            timeout_s=self._timeout_s,
            logger=log.engine,
            call_name="turn_achievement_writer",
        )
        if outcome.result is None:  # timeout or provider error — already logged
            return DEFAULT_ACHIEVEMENT

        criterion = (outcome.result.content or "").strip().strip('"').strip()
        if not criterion:
            log.engine.warning(
                "turn_achievement_writer.write: empty criterion — fail-safe to the constant",
                extra={"_fields": {"achievement": DEFAULT_ACHIEVEMENT}},
            )
            return DEFAULT_ACHIEVEMENT

        criterion = criterion[: self.MAX_CRITERION_CHARS].strip()

        # 2. DECISION — the degeneracy guard. A criterion that references nothing
        # from the request describes delivery, not the goal, and is worthless as a
        # completion test. Structural and language-independent by design.
        if not _references(criterion, request):
            log.engine.warning(
                "turn_achievement_writer.write: criterion references nothing from the "
                "request — refusing it and failing safe to the constant",
                extra={"_fields": {
                    "refused": criterion[:_LOG_TEXT_CHARS],
                    "request": request[:_LOG_TEXT_CHARS],
                    "achievement": DEFAULT_ACHIEVEMENT,
                }},
            )
            return DEFAULT_ACHIEVEMENT

        # 4. EXIT — INFO, because this line is the evidence for the shadow phase:
        # how often a real criterion is written, and what it says.
        log.engine.info(
            "turn_achievement_writer.write: criterion written",
            extra={"_fields": {
                "achievement": criterion[:_LOG_TEXT_CHARS],
                "request": request[:_LOG_TEXT_CHARS],
                "specific": True,
            }},
        )
        return criterion
