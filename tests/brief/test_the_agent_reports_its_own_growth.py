"""N01 — the agent says how it is changing, and cannot flatter itself.

BAKIR, 2026-08-10, verbatim and outside the reference map: "i am not going to
create a next chatbot which does no interaction. thats why i am thinking to build
jarvis which will dream and rethink about his life, his abilities, his growing,
learning, improving and etc things."

NOTHING SAID ANY OF IT. The platform reflects on every TURN — 6,419 reflections
promoted to 5,769 lessons — and evolves its own DNA (586 artifacts). What no
surface did was compare the platform to ITSELF. AutonomicHealth reports current
counts, Learning reports the last 24 hours, SystemSpend reports tokens; not one
could say "I am better at this than I was".

MEASURED BEFORE BUILDING, which is what made it worth building: turn success went
28.9% -> 66.9% -> 89.5% across three consecutive weeks, browser_navigate failures
60 -> 18, web_fetch 27 -> 48 and shell 20 -> 35 (both worse), and 22 skills
learned against 0 the week before. Real movement in both directions.

DETERMINISTIC ON PURPOSE. The obvious build is to hand a model its own history and
let it narrate. That produces a paragraph about growth whether or not any growth
happened — the overclaim shape this platform has spent weeks learning not to make.
The sentence is assembled FROM the measurements, so a bad week reads as a bad
week. The tests below exist mostly to hold that property.
"""

from __future__ import annotations

import time

import pytest

from stackowl.brief.assemblers import GrowthAssembler
from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID

pytestmark = pytest.mark.asyncio


async def _outcome(
    db, *, days_ago: float, success: int, cap: str = "", failure_class: str = "",
) -> None:
    await db.execute(
        "INSERT INTO task_outcomes (trace_id, session_key, owl_name, channel, success,"
        " latency_ms, tool_call_count, captured_at, owner_id, failed_capability,"
        " failure_class) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (f"t{time.time_ns()}", "s", "secretary", "telegram", success,
         1.0, 0, time.time() - days_ago * 86400.0, DEFAULT_PRINCIPAL_ID, cap,
         failure_class),
    )


async def test_a_week_that_got_BETTER_says_so(tmp_db) -> None:
    for _ in range(10):
        await _outcome(tmp_db, days_ago=10, success=0)
    for _ in range(10):
        await _outcome(tmp_db, days_ago=1, success=1)

    sec = await GrowthAssembler(tmp_db).assemble(None)

    assert not sec.omitted
    assert "better at my job" in sec.items[0]
    assert "100%" in sec.items[0] and "0%" in sec.items[0]


async def test_a_week_that_got_WORSE_is_not_dressed_up(tmp_db) -> None:
    """The property the whole design exists to hold. A narrated version would be
    free to find something encouraging to say; this one cannot."""
    for _ in range(10):
        await _outcome(tmp_db, days_ago=10, success=1)
    for _ in range(10):
        await _outcome(tmp_db, days_ago=1, success=0)

    sec = await GrowthAssembler(tmp_db).assemble(None)

    assert "worse at my job" in sec.items[0]


async def test_a_capability_that_REGRESSED_is_always_named(tmp_db) -> None:
    """A self-assessment that only lists progress is the overclaim shape wearing a
    friendly voice."""
    for _ in range(6):
        await _outcome(tmp_db, days_ago=10, success=0, cap="browser_navigate")
    for _ in range(6):
        await _outcome(tmp_db, days_ago=1, success=0, cap="shell")

    sec = await GrowthAssembler(tmp_db).assemble(None)
    body = " ".join(sec.items)

    assert "I got WORSE at: shell" in body
    assert "browser_navigate" in body, "an improvement disappeared from the report"


async def test_a_TINY_capability_change_is_not_reported_as_progress(tmp_db) -> None:
    """"2 failures became 1" is a 50% improvement and means nothing. Without the
    floor this section would fill up with noise and stop being read."""
    for _ in range(2):
        await _outcome(tmp_db, days_ago=10, success=0, cap="tts")
    await _outcome(tmp_db, days_ago=1, success=0, cap="tts")

    sec = await GrowthAssembler(tmp_db).assemble(None)

    assert "tts" not in " ".join(sec.items)


async def test_with_NO_PRIOR_WEEK_it_says_nothing_rather_than_everything(tmp_db) -> None:
    """A first week has nothing to be better than. Treating an absent baseline as
    zero would report spectacular growth for ever — the 0-over-0 trap."""
    for _ in range(5):
        await _outcome(tmp_db, days_ago=1, success=1)

    sec = await GrowthAssembler(tmp_db).assemble(None)

    assert sec.omitted


async def test_an_unreadable_store_omits_the_section_not_the_BRIEF(tmp_db) -> None:
    """One section may never take the whole brief down with it."""
    class _Boom:
        async def fetch_all(self, *a: object, **k: object) -> list:
            raise RuntimeError("db gone")

    sec = await GrowthAssembler(_Boom()).assemble(None)
    assert sec.omitted


async def test_a_capability_that_STUMBLED_but_RECOVERED_is_not_a_failure(tmp_db) -> None:
    """The correctness of the whole per-capability number.

    A row can name a failed_capability on a turn that SUCCEEDED — the capability
    stumbled and the recovery ladder got there anyway. Counting those reports a
    regression the user never experienced.

    MEASURED 2026-09-03, hours after this section first shipped counting every
    row: web_fetch read 27 -> 48, of which 22 and 37 were on turns that
    SUCCEEDED; the honest figures are 5 -> 11. browser_navigate read 60 -> 18
    against a true 12 -> 3, and `memory` and `read_file` appeared as improvements
    on 2 and 1 real failures. The direction survived for every capability; the
    magnitudes did not.
    """
    # Prior week: 6 genuine failures. This week: 1 genuine failure and 20 turns
    # where web_fetch stumbled and the turn STILL SUCCEEDED.
    for _ in range(6):
        await _outcome(tmp_db, days_ago=10, success=0, cap="web_fetch")
    await _outcome(tmp_db, days_ago=1, success=0, cap="web_fetch")
    for _ in range(20):
        await _outcome(tmp_db, days_ago=1, success=1, cap="web_fetch")

    sec = await GrowthAssembler(tmp_db).assemble(None)
    body = " ".join(sec.items)

    assert "I got WORSE at" not in body, (
        "recovered stumbles were counted as failures — the section reports a "
        "regression the user never experienced"
    )
    assert "web_fetch (6→1)" in body, body


async def test_running_out_of_STEPS_is_not_blamed_on_a_capability(tmp_db) -> None:
    """The biggest correction this section needed, and the reason it is separate.

    `failure_class='stop'` means the turn RAN OUT OF STEPS. The capability named
    on such a row is whatever was in flight, not one that failed. MEASURED
    2026-09-03 on live data, excluding it:

        web_fetch          5 -> 11  became  0 -> 0   (never failed at all)
        browser_navigate  12 ->  3  became  0 -> 0   ("improvement" was not real)
        shell             15 -> 21  became 11 -> 10  (flat, not a regression)

    Every per-capability movement the section reported on its first night was
    step exhaustion wearing a tool's name. A lesson telling an owl to be careful
    with web_fetch cannot fix running out of steps — which is exactly why the
    mined `incident_web_fetch_stop` lesson was one the recurrence detector
    reported as "not holding".
    """
    for _ in range(8):
        await _outcome(tmp_db, days_ago=1, success=0, cap="web_fetch", failure_class="stop")
    for _ in range(6):
        await _outcome(tmp_db, days_ago=10, success=0, cap="web_fetch", failure_class="stop")
    # one genuine capability failure each week, so the section still has turns
    await _outcome(tmp_db, days_ago=1, success=1)
    await _outcome(tmp_db, days_ago=10, success=1)

    sec = await GrowthAssembler(tmp_db).assemble(None)
    body = " ".join(sec.items)

    assert "web_fetch" not in body, (
        "a step-cap exhaustion was blamed on the tool that happened to be running"
    )


async def test_the_step_CEILING_is_reported_as_its_own_thing(tmp_db) -> None:
    """It is the number that actually moved (31 -> 48 live), and it is about the
    platform rather than any tool. Dropping `stop` without reporting it would
    have traded a wrong answer for silence."""
    for _ in range(8):
        await _outcome(tmp_db, days_ago=1, success=0, cap="shell", failure_class="stop")
    for _ in range(3):
        await _outcome(tmp_db, days_ago=10, success=0, cap="shell", failure_class="stop")
    await _outcome(tmp_db, days_ago=1, success=1)
    await _outcome(tmp_db, days_ago=10, success=1)

    body = " ".join((await GrowthAssembler(tmp_db).assemble(None)).items)

    assert "stopped before finishing 8 times, up from 3" in body
    assert "my own limits" in body
    assert "steps" not in body, (
        "failure_class='stop' is a step-cap breach, a TOKEN-cap breach and other "
        "early stops under one label — measured 2026-09-03, recent breaches are "
        "the 500,000-token cap, not the 20-step one. Naming a ceiling the data "
        "cannot identify is the overclaim this section exists to avoid."
    )
