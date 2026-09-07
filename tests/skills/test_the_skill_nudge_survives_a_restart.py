"""A nudge counter that resets faster than its interval can never fire.

WHY THIS EXISTS — D09.4's mechanism has fired ZERO times, ever, and the recorded reason
was wrong.

`[skills] nudge` matches both the "due" and the "due but SUPPRESSED" lines, and BOTH are
zero across every retained log. D09.4's note said it was "waiting on a DEEP lane". It is
not. MEASURED 2026-09-07 over six days of logs:

  * 291 core boots against 429 turns — the platform restarts almost as often as it takes
    a turn;
    WHAT THOSE BOOTS ACTUALLY ARE — MEASURED 2026-09-07, and it corrects the sentence
    above rather than the fix below. "The platform restarts almost as often as it takes
    a turn" reads as a property of the PLATFORM. It is a property of DEVELOPING ON THE
    LIVE INSTANCE. Across eleven retained days: 715 boots, of which **504 were
    CodeWatcher responding to a `src/` edit** and 497 reached the exec — 70%, this loop
    editing the tree it is measuring. On 2026-09-07: 32 boots, 26 of them CodeWatcher,
    and the remaining 6 match the `./start.sh` runs made by hand that day.

    In a deployment where nobody edits `src/`, CodeWatcher never fires and roughly seven
    of every ten of those boots do not happen. The seeding fix below is still right —
    reading a durable count is better than an in-process one whatever the restart rate —
    but the PREMISE was measuring the observer.
  * the deepest (boot, lane) pair reached NINE turns;
  * the skill nudge's interval is TEN;
  * so ZERO pairs reached it, ever, while FIFTEEN reached four — which is exactly why the
    memory nudge at interval 4 has fired 99 times and this one has fired none.

`infra/nudge.py` chose an in-process counter deliberately and said "the intervals absorb
it", citing 34 boots in a day. Measured at ~48 boots a day, that absorption is real at 4
and false at 10. The interval and the reset rate are not independent, and nothing checked
that they still stood in the right relation.

THE FACT ALREADY EXISTS DURABLY. `sessions.completed_turns` is the per-lane turn count,
it survives restarts, and it reaches 10 on 13 of 129 sessions with a maximum of 46. The
nudge was keeping a second, weaker copy of a number the platform already stores — the same
two-copies-of-one-fact shape as DEBT-164, where the copy that moved was not the one anyone
read.

SEEDING, NOT REPLACING, and that choice is deliberate. The in-process counter still owns
suppression (`due but SUPPRESSED` when the tool is unreachable) and reset-on-action
(`note_skill_written`). Seeding it from the durable count on FIRST sight of a lane fixes
exactly the erasure and leaves every other behaviour untouched. The known imperfection is
stated rather than hidden: a restart re-seeds from a count that keeps climbing, so a lane
that just wrote a skill can be nudged again sooner than the interval intends. That is a
mild over-nudge against a mechanism that currently never fires at all.
"""

from __future__ import annotations

from stackowl.infra.nudge import TurnNudge


class TestSeedingFromTheDurableCount:
    def test_a_lane_already_past_the_interval_is_due_immediately(self) -> None:
        """THE DEFECT. Before seeding, a restart put every lane back to zero, and no
        lane has ever survived ten turns inside one boot."""
        nudge = TurnNudge(interval=10, text="write a skill", label="[t] nudge")

        assert nudge.note_turn("lane-a", seed=46) == "write a skill"

    def test_without_a_seed_it_counts_from_zero_as_before(self) -> None:
        """The memory nudge passes no seed and must be byte-for-byte unchanged."""
        nudge = TurnNudge(interval=4, text="remember", label="[t] nudge")

        assert [nudge.note_turn("lane-b") for _ in range(4)] == [None, None, None, "remember"]

    def test_the_seed_applies_only_on_first_sight(self) -> None:
        """A seed re-applied every turn would re-fire forever once the lane is deep."""
        nudge = TurnNudge(interval=10, text="x", label="[t] nudge")
        first = nudge.note_turn("lane-c", seed=20)
        rest = [nudge.note_turn("lane-c", seed=20) for _ in range(3)]

        assert first == "x"
        assert rest == [None, None, None], "the seed re-fired after the lane was known"

    def test_a_seed_below_the_interval_still_waits(self) -> None:
        """Seeding is not a shortcut past the interval — nine turns is still nine."""
        nudge = TurnNudge(interval=10, text="x", label="[t] nudge")

        assert nudge.note_turn("lane-d", seed=8) is None

    def test_a_missing_or_absurd_seed_never_breaks_the_turn(self) -> None:
        """A reminder may not cost a turn its answer — the module's standing rule."""
        nudge = TurnNudge(interval=10, text="x", label="[t] nudge")

        assert nudge.note_turn("lane-e", seed=None) is None
        assert nudge.note_turn("lane-f", seed=-5) is None

    def test_suppression_still_holds_a_seeded_lane_due(self) -> None:
        """The gate and the count stay separate — conflating them is what made this
        unfireable the first time. A seeded lane whose tool is unreachable STAYS due."""
        nudge = TurnNudge(interval=10, text="x", label="[t] nudge")
        suppressed = nudge.note_turn("lane-g", deliverable=False, seed=30)

        assert suppressed is None
        assert nudge.note_turn("lane-g", deliverable=True) == "x", (
            "a suppressed turn discarded the count instead of staying due"
        )

    def test_reset_on_action_still_works_on_a_seeded_lane(self) -> None:
        nudge = TurnNudge(interval=10, text="x", label="[t] nudge")
        assert nudge.note_turn("lane-h", seed=40) == "x"
        nudge.note_action("lane-h")

        assert nudge.note_turn("lane-h", seed=40) is None, (
            "the lane was re-seeded after an action reset it"
        )


class TestTheSkillNudgePassesTheDurableCount:
    """Built-but-not-wired is defect shape 5 here, and it would be invisible: the
    mechanism would work in a unit test and still never fire in production."""

    def test_the_skill_nudge_accepts_and_forwards_a_seed(self) -> None:
        from stackowl.skills import nudge as skill_nudge

        skill_nudge.reset()
        text = skill_nudge.note_turn("lane-i", frozenset({"skill_manage"}), turns_so_far=25)

        assert text is not None, "the skill nudge ignored the durable turn count"

    def test_the_call_site_reads_the_session_store(self) -> None:
        """The seed has to come from somewhere real, and reach THE NUDGE.

        NAMES THE CALLEE, and the first version did not. It asked whether ANY call in
        `execute.py` passed `turns_so_far`, which the three `_turn_context_prefix(...)`
        calls satisfy — so cutting the actual link to the nudge left this green and the
        mutant SURVIVED. A guard against built-but-not-wired, satisfied by a neighbouring
        wire. Found by mutating, not by reading.
        """
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "src/stackowl/pipeline/steps/execute.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        seeded_nudge_calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and "note_turn" in n.func.id
            and any(kw.arg == "turns_so_far" for kw in n.keywords)
        ]

        assert seeded_nudge_calls, (
            "execute.py calls the skill nudge without turns_so_far, so the durable "
            "count never reaches it and it stays unfireable in production — which is "
            "the entire defect, restored"
        )
        assert "session_store" in src, "execute.py does not reach the session store"

    def test_compile_not_just_parse(self) -> None:
        """`ast.parse` is NOT a sufficient syntax gate and this item proved it.

        A scripted edit here duplicated `turns_so_far=` at two call sites. Repeated
        keyword arguments are rejected at COMPILE time, not at parse time, so
        `ast.parse()` — the gate CLAUDE.md prescribes — reported the file clean while
        `compile()` rejected it. The tests passed too, because none of them imports
        this module.
        """
        from pathlib import Path

        src = (Path(__file__).resolve().parents[2]
               / "src/stackowl/pipeline/steps/execute.py").read_text(encoding="utf-8")

        compile(src, "execute.py", "exec")
