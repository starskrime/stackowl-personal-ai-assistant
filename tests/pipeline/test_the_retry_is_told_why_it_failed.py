"""A constrained retry has to be TOLD the constraint.

BAKIR, 2026-08-20, on the loop finally re-driving blocked work. MEASURED that night,
task 86de5841:

    01:56:56  the turn was blocked on a capability — returning it to the loop
              {'blocked': 'shell'}
    01:56:56  task requeued with what failed   failure_class: blocked_capability
    01:57:03  loop claim -> re-driving through the retry actuator
    02:05:24  owl_build action='edit'  ->  clamped: ['shell']
    02:05:35  task COMPLETE

The loop did everything right: it saw the block, requeued with the reason, claimed
the row and re-drove it for eight and a half minutes. And the retry reached for
``edit`` — which by design can NEVER widen authority — instead of ``grant``, which
exists precisely to do it. ``grant`` was called zero times.

WHY. ``fail_and_requeue`` stores the reason on the row as ``last_error``, including
the remedy in as many words ("ask the user to grant it — owl_build action='grant'").
``_augment_goal`` then builds the retry's prompt from ``banned_capabilities`` ALONE
and never reads ``last_error``. The explanation was written to the database and
never shown to the model.

So the loop's promise — Bakir's own words, "adding previous failure or action
details... so next loop when it picks it, it also looks: is any previous one? Yes —
learn from that experience" — was half kept: it passed WHICH capability burned, and
dropped WHY and WHAT TO DO INSTEAD. A retry that cannot see the reason is the blind
retry the design rejects, wearing the clothes of a constrained one.

This is the third write-with-no-reader in one session (``should_decompose``,
``describe_job_destination``, and now this), which is why the rule is: after wiring a
fact, verify it survives the boundary it has to cross.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _Row:
    def __init__(self, goal: str, banned: list[str] | None = None,
                 last_error: str | None = None, attempt_count: int = 1) -> None:
        self.goal = goal
        self.banned_capabilities = banned or []
        self.last_error = last_error
        self.attempt_count = attempt_count


def _augment(row: _Row) -> str:
    from stackowl.pipeline.retry_actuator import RetryActuator

    return RetryActuator._augment_goal(object(), row)  # type: ignore[arg-type]


class TestTheRetrySeesWhyTheLastAttemptFailed:
    async def test_the_reason_reaches_the_prompt(self) -> None:
        """The exact case: the row knew it was blocked on shell and knew the
        remedy, and the model was shown neither."""
        text = _augment(_Row(
            goal="give mailbutler full capabilities",
            last_error=(
                "the turn was BLOCKED: `shell` is not permitted for this owl... "
                "ask the user to grant it — owl_build action='grant'"
            ),
        ))

        assert "shell" in text
        assert "grant" in text
        assert "give mailbutler full capabilities" in text

    async def test_the_reason_is_included_even_with_no_banned_capabilities(
        self,
    ) -> None:
        """A blocked turn bans nothing — the tool never ran. Under the old code
        that meant the goal went back VERBATIM, with no hint at all."""
        text = _augment(_Row(goal="do the thing", last_error="it was BLOCKED on shell"))

        assert "shell" in text
        assert text != "do the thing"

    async def test_banned_capabilities_are_still_carried(self) -> None:
        """The half that already worked must not be lost."""
        text = _augment(_Row(goal="do the thing", banned=["web_fetch"]))

        assert "web_fetch" in text

    async def test_both_are_carried_together(self) -> None:
        text = _augment(_Row(
            goal="do the thing", banned=["web_fetch"], last_error="blocked on shell",
        ))

        assert "web_fetch" in text
        assert "shell" in text


class TestItStaysSaneAtTheEdges:
    async def test_a_clean_first_attempt_is_unchanged(self) -> None:
        """No prior failure means nothing to say, and the goal must go through
        byte-for-byte."""
        assert _augment(_Row(goal="just do it")) == "just do it"

    async def test_a_huge_error_does_not_swamp_the_goal(self) -> None:
        """last_error holds up to 2000 chars. Pasting all of it in front of a short
        ask would drown the thing being asked for — the unbounded-prose failure the
        structured `banned_capabilities` design was chosen to avoid."""
        text = _augment(_Row(goal="short ask", last_error="x" * 2000))

        assert "short ask" in text
        assert len(text) < 900
