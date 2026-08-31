"""Bakir: "I want the total token consumption report every 24 hours for each type ...
that will give visibility to user what is happening in system."

He asked after being shown this, which he had no way to see for himself::

    incident (RCA)          15,724,829   72.3%
    goal                     2,508,715   11.5%
    job:reflection_writer    1,034,839    4.8%
    owl:*:telegram             973,105    4.5%
    owl:*:recovery             654,570    3.0%
    <his own chat>             544,216    2.5%
                            ----------
    TOTAL                   21,744,608

His own conversations were 2.5% of the day; the self-healing loop was 72.3%. Nothing
in the platform reported that, so the only reason it was ever seen is that he asked
a question on the night the bill got big enough to notice.

IT EXTENDS THE BRIEF THAT ALREADY RUNS DAILY. `MorningBriefHandler` already
assembles sections, already renders them, and already delivers through the single
`ProactiveDeliverer` seam with an honest per-channel status. A second daily
reporting job would be a second engine for a thing that exists — the rule this
codebase states as "find the existing loop and extend it".

IT SHIPS ON. `_run_assembler` reads `toggles.get(key, True)`, so a section absent
from `settings.brief.sections` is ENABLED. No operator switch to find.

ONE RULE FOR "WHAT KIND OF LANE IS THIS". `lane_family` lives beside
`is_machine_lane` in sessions/models.py, because that module already owns what a
lane key means and this report is the fourth place tonight that needed to say it.
"""

from __future__ import annotations

import time

import pytest

from stackowl.brief.assemblers import BriefContext, SystemSpendAssembler
from stackowl.config.settings import Settings
from stackowl.db.pool import DbPool
from stackowl.sessions.models import lane_family

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ lane_family


def test_lane_family_names_the_kinds_that_actually_appear() -> None:
    """Every shape measured on the live database on 2026-08-31."""
    assert lane_family("incident-3859302db13c") == "incident"
    assert lane_family("owl:secretary:recovery:task-74e6b23") == "recovery"
    assert lane_family("owl:secretary:objective:obj-1ad8a5fa") == "objective"
    assert lane_family("goal-goal_execution-88f54aa1") == "goal"
    assert lane_family("job:reflection_writer-ee748779") == "job:reflection_writer"
    assert lane_family("owl:secretary:telegram:dm:72055773") == "you (telegram)"
    assert lane_family("72055773") == "you (telegram)"


def test_a_lane_it_cannot_read_is_named_not_dropped() -> None:
    """A bucket called "other" that quietly holds 40% would be worse than no report."""
    assert lane_family("") == "other"
    assert lane_family("something-unexpected") == "something"


# ------------------------------------------------------------------ the section


async def _spend(db: DbPool, session_key: str, tokens: int, *, age_h: float = 1.0) -> None:
    await db.execute(
        "INSERT INTO cost_records (provider_name, model, input_tokens, output_tokens,"
        " cost_usd, trace_id, recorded_at, owner_id, session_key, priced)"
        " VALUES ('p','m',?,0,0.0,'t',?, 'principal-default', ?, 1)",
        (tokens, _iso(age_h), session_key),
    )


def _iso(age_h: float) -> str:
    import datetime as dt

    return (dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_h)).isoformat()


def _ctx() -> BriefContext:
    return BriefContext(job_id="j-1", last_brief_time=None, settings=Settings())


async def test_the_section_reports_each_lane_family(tmp_db: DbPool) -> None:
    """The live shape, reproduced: the self-healing loop dwarfing the human."""
    await _spend(tmp_db, "incident-aaa", 15_000_000)
    await _spend(tmp_db, "owl:secretary:telegram:dm:72055773", 500_000)
    await _spend(tmp_db, "goal-goal_execution-88f", 2_500_000)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    body = "\n".join(section.items)
    assert "incident" in body and "goal" in body and "you (telegram)" in body
    assert section.omitted is False


async def test_it_reports_the_SHARE_not_only_the_count(tmp_db: DbPool) -> None:
    """72.3% is the number that made the problem visible. A raw token count needs
    the reader to do arithmetic before it means anything."""
    await _spend(tmp_db, "incident-aaa", 900_000)
    await _spend(tmp_db, "72055773", 100_000)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    assert any("90" in i and "%" in i for i in section.items), section.items


async def test_the_biggest_consumer_comes_FIRST(tmp_db: DbPool) -> None:
    """The point of the report is what dominates; alphabetical order would bury it."""
    await _spend(tmp_db, "72055773", 100_000)
    await _spend(tmp_db, "incident-aaa", 900_000)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    # items[0] is the TOTAL headline — the number he reacted to — so the first
    # FAMILY row is items[1].
    assert "Total" in section.items[0]
    assert "incident" in section.items[1]


async def test_spend_OUTSIDE_the_window_is_not_counted(tmp_db: DbPool) -> None:
    """"every 24 hours" — a running total would stop meaning anything by week two."""
    await _spend(tmp_db, "incident-aaa", 900_000, age_h=48.0)
    await _spend(tmp_db, "72055773", 1_000)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    assert not any("incident" in i for i in section.items), section.items


async def test_a_QUIET_day_still_reports(tmp_db: DbPool) -> None:
    """Silence must be distinguishable from a broken report — the same rule the
    brief's own F-79 empty-recall item already follows."""
    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    assert section.omitted is False
    assert section.items, "a quiet day still has to say so"


async def test_a_BROKEN_query_does_not_break_the_brief(tmp_db: DbPool, monkeypatch) -> None:  # noqa: ANN001
    """The handler wraps every assembler, but a section that raises turns into an
    error block in the operator's message. Answering honestly is better."""
    async def _boom(*a: object, **k: object) -> list:
        raise RuntimeError("cost_records unreadable")

    monkeypatch.setattr(tmp_db, "fetch_all", _boom)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    assert section.omitted is False
    assert any("could not" in i.lower() or "unavailable" in i.lower() for i in section.items)


async def test_the_TOTAL_is_stated(tmp_db: DbPool) -> None:
    """21,744,608 is the number he reacted to. A per-type table without the total
    makes the reader add up six rows to find out whether to care."""
    await _spend(tmp_db, "incident-aaa", 900_000)
    await _spend(tmp_db, "72055773", 100_000)

    section = await SystemSpendAssembler(tmp_db).assemble(_ctx())

    assert any("1,000,000" in i for i in section.items), section.items


def test_the_brief_actually_INCLUDES_the_section() -> None:
    """A section nothing assembles is a report nobody gets — the defect class this
    codebase keeps paying for. Structural, over the handler's own source."""
    import inspect

    from stackowl.scheduler.handlers import morning_brief

    source = inspect.getsource(morning_brief)
    assert "SystemSpendAssembler(db=db)" in source, (
        "the spend section is never assembled, so the brief cannot carry it"
    )


def test_it_needs_no_settings_entry_to_appear() -> None:
    """Ships ON. `_run_assembler` reads `toggles.get(key, True)`, so a section
    absent from settings.brief.sections is ENABLED — no switch to find."""
    from stackowl.config.settings import Settings

    assert "system_spend" not in Settings().brief.sections
