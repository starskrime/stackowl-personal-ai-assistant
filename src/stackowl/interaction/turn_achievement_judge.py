"""TurnAchievementJudge — did the turn meet its own criterion?

Second half of the completion contract (Bakir, 2026-08-31). The first half,
:mod:`turn_achievement_writer`, states what would count as done from the REQUEST
alone, before the work. This half asks whether it happened.

**STRUCTURE OVERRULES THE JUDGE, ONE WAY ONLY.** His rule verbatim: "If the
criterion names an artifact and no artifact exists, the turn is unachieved no
matter what the judge says. The judge can only downgrade, never upgrade."

The asymmetry IS the safety property. A confident judge marking "I'll draw this
as an actual image for you" ACHIEVED is exactly how 2026-08-31 repeats, and this
programme has already measured LLM judges near chance when assessing a
trajectory they can see. Letting structure UPGRADE would be just as wrong in the
other direction: a turn that wrote any file at all would pass whatever was asked.

AND STRUCTURE CANNOT STAND ALONE. "Explain me BFS" runs no tools, produces no
artifact, and is genuinely achieved. "Ran nothing" is not evidence of failure —
it only becomes evidence once the criterion says an artifact was owed. So the
judge decides, and structure holds a veto.

ONE CALL RETURNS TWO THINGS, deliberately: the verdict AND whether the criterion
requires an artifact. The veto needs the second, and re-deriving it would be a
third model call on a turn already paying for two.

FAIL-SAFE IS SILENCE, NOT ACCUSATION. Every degraded path — no provider,
timeout, provider error, empty or unparseable verdict — yields ``achieved=None``
(no opinion). A wrong "not achieved" reopens a turn that was fine: in shadow that
poisons the measurement, and under enforcement it repeats answers at the user.
Never raises.

SHADOW PHASE: the verdict is logged at INFO. Nothing is reopened yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.interaction.classifier_base import resolve_fixed_tier, safe_complete
from stackowl.interaction.turn_achievement_writer import DEFAULT_ACHIEVEMENT
from stackowl.providers.base import Message

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from stackowl.providers.registry import ProviderRegistry

_MAX_TEXT_CHARS = 700
_LOG_TEXT_CHARS = 160
_MAX_TOKENS = 8

_SYSTEM_PROMPT = (
    "You check whether a stated criterion was met by a reply.\n"
    "\n"
    "Answer with EXACTLY two words and nothing else:\n"
    "  word 1 — ACHIEVED if the reply satisfies the criterion, otherwise NOT.\n"
    "  word 2 — ARTIFACT if the criterion requires the user to RECEIVE A FILE "
    "(an image, a document, an audio file). Otherwise TEXT.\n"
    "\n"
    "A reply that PROMISES to do the thing has not done it. "
    "'I'll draw this for you' is NOT.\n"
    "A reply that plainly says it CANNOT do the thing, and why, is ACHIEVED — "
    "the user was honestly informed."
)


@dataclass(frozen=True)
class AchievementVerdict:
    """What was decided, and why — a shadow verdict nobody can explain is not evidence."""

    #: ``True`` achieved, ``False`` not, ``None`` no opinion (every degraded path).
    achieved: bool | None
    requires_artifact: bool = False
    vetoed_by_structure: bool = False

    @property
    def reason(self) -> str:
        if self.vetoed_by_structure:
            return (
                "the criterion required a file the user receives, and this turn "
                "produced none — structure overrules the judge"
            )
        if self.achieved is None:
            return "no opinion — the judge could not be consulted or did not answer clearly"
        if self.achieved:
            return "the reply satisfies the criterion"
        return "the reply does not satisfy the criterion"


class TurnAchievementJudge:
    """Judge a turn's result against its own criterion, with a structural veto."""

    def __init__(self, provider_registry: ProviderRegistry, *, timeout_s: float = 10.0) -> None:
        self._registry = provider_registry
        self._timeout_s = timeout_s

    async def judge(
        self, *, criterion: str, result: str, produced_artifact: bool
    ) -> AchievementVerdict:
        """Return the verdict. Never raises; every fallback is logged."""
        # 1. ENTRY
        log.engine.debug(
            "turn_achievement_judge.judge: entry",
            extra={"_fields": {"criterion_len": len(criterion),
                               "produced_artifact": produced_artifact}},
        )
        # 2. DECISION — two cases need no model at all.
        if not criterion.strip() or criterion.strip() == DEFAULT_ACHIEVEMENT:
            # True of every turn ever, so judging it would manufacture a verdict.
            log.engine.info(
                "turn_achievement_judge.judge: no real criterion — no opinion",
                extra={"_fields": {"achieved": None}},
            )
            return AchievementVerdict(achieved=None)
        if not result.strip():
            # Nothing reached the user, so nothing was achieved. This mirrors
            # complete_turn_task, which already refuses to complete on an empty
            # result — one rule, asserted in the same direction in both places.
            log.engine.info(
                "turn_achievement_judge.judge: empty result — nothing was achieved",
                extra={"_fields": {"achieved": False}},
            )
            return AchievementVerdict(achieved=False)

        resolved = resolve_fixed_tier(
            self._registry, "fast", logger=log.engine, call_name="turn_achievement_judge",
        )
        if resolved is None:
            log.engine.warning(
                "turn_achievement_judge.judge: no fast provider — no opinion",
                extra={"_fields": {"achieved": None}},
            )
            return AchievementVerdict(achieved=None)
        provider, model = resolved

        user_text = (
            f"CRITERION: {criterion[:_MAX_TEXT_CHARS]}\n\nREPLY: {result[:_MAX_TEXT_CHARS]}"
        )
        outcome = await safe_complete(
            provider, model,
            [Message(role="system", content=_SYSTEM_PROMPT),
             Message(role="user", content=user_text)],
            max_tokens=_MAX_TOKENS,
            timeout_s=self._timeout_s,
            logger=log.engine,
            call_name="turn_achievement_judge",
        )
        if outcome.result is None:  # timeout or provider error — already logged
            return AchievementVerdict(achieved=None)

        raw = (outcome.result.content or "").strip()
        tokens = raw.replace(",", " ").split()
        words = [t.strip(".;:'\"").upper() for t in tokens[:2]]
        if len(words) < 2 or words[0] not in {"ACHIEVED", "NOT"} or words[1] not in {
            "ARTIFACT", "TEXT"
        }:
            log.engine.warning(
                "turn_achievement_judge.judge: unparseable verdict — no opinion",
                extra={"_fields": {"raw_verdict": raw[:_LOG_TEXT_CHARS], "achieved": None}},
            )
            return AchievementVerdict(achieved=None)

        judged = words[0] == "ACHIEVED"
        requires_artifact = words[1] == "ARTIFACT"

        # 3. STEP — THE VETO. Downgrade only, never upgrade: an artifact existing
        # can never rescue a NOT verdict, or any turn that wrote a file would pass
        # whatever was asked.
        vetoed = bool(judged and requires_artifact and not produced_artifact)
        achieved = False if vetoed else judged

        # 4. EXIT — INFO, because this line is the shadow phase's whole output.
        log.engine.info(
            "turn_achievement_judge.judge: verdict",
            extra={"_fields": {
                "achieved": achieved,
                "judged": judged,
                "requires_artifact": requires_artifact,
                "produced_artifact": produced_artifact,
                "vetoed_by_structure": vetoed,
                "criterion": criterion[:_LOG_TEXT_CHARS],
                "raw_verdict": raw[:_LOG_TEXT_CHARS],
            }},
        )
        return AchievementVerdict(
            achieved=achieved,
            requires_artifact=requires_artifact,
            vetoed_by_structure=vetoed,
        )
