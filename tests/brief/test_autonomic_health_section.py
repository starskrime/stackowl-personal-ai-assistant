"""ADR-19 — the platform reports on its own closed loops, proactively.

The single most expensive consequence of ADR-19's findings was not any one
defect. It was the SILENCE: 409 RCAs discarded to a parser, 265 duplicate skills
minted, a catalog 92% dead — all of it discoverable only by running ad-hoc log
queries at 2am, which is literally how it was found.

A loop nobody can see is a loop nobody maintains.
"""

from pathlib import Path

import pytest

from stackowl.brief.assemblers import AutonomicHealthAssembler, BriefContext
from stackowl.config.settings import Settings
from stackowl.skills.loader import LoadedSkill
from stackowl.skills.manifest import SkillManifest
from stackowl.skills.store import SkillIndexStore


def _ctx() -> BriefContext:
    return BriefContext(job_id="brief-1", last_brief_time=None, settings=Settings())


def _loaded(name: str, source: str = "learned") -> LoadedSkill:
    return LoadedSkill(
        manifest=SkillManifest(name=name, description="d", source=source),
        path=Path("/tmp/x"), body="b", tools_registered=0, owls_registered=0, tool_names=(),
    )


@pytest.mark.asyncio
async def test_reports_the_catalog_shape(tmp_db):
    store = SkillIndexStore(tmp_db)
    a = await store.upsert(_loaded("a"))
    b = await store.upsert(_loaded("b"))
    await store.upsert(_loaded("c"))
    await store.set_lifecycle_state(b, "stale", 1.0)
    await store.increment_n_executions(a)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert not section.omitted
    assert "skills active:2 stale:1 archived:0" in section.items
    # The number that says whether the catalog is EARNING its size.
    assert "skills_ever_used:1/3" in section.items


@pytest.mark.asyncio
async def test_archived_skills_are_reported_not_hidden(tmp_db):
    """Retirement is the loop DOING something. If the brief doesn't say so, the
    operator finds out by noticing a skill missing."""
    store = SkillIndexStore(tmp_db)
    sid = await store.upsert(_loaded("gone"))
    await store.set_lifecycle_state(sid, "archived", 1.0)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert any("archived:1" in item for item in section.items)


@pytest.mark.asyncio
async def test_failing_background_jobs_are_named(tmp_db):
    """A failing autonomic job is the failure mode that hides every other one:
    the loop stops, and its silence reads as 'nothing to report'."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    for _ in range(3):
        await tmp_db.execute(
            "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
            "VALUES (?, datetime('now'), 'failed', 'boom', 1.0)",
            ("dream-worker",),
        )
    await tmp_db.execute(
        "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
        "VALUES (?, datetime('now'), 'success', 'ok', 1.0)",
        ("heartbeat",),
    )

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert "jobs_24h ran:4 failed:3" in section.items
    assert "failing:dream-worker x3" in section.items


@pytest.mark.asyncio
async def test_old_job_failures_are_not_reported_as_todays(tmp_db):
    """A 24h window that silently included ancient history would make the brief
    permanently alarming and therefore permanently ignored."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await tmp_db.execute(
        "INSERT INTO job_results (job_id, run_at, status, result_text, duration_ms) "
        "VALUES (?, datetime('now', '-5 days'), 'failed', 'old', 1.0)",
        ("ancient-job",),
    )

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert not any("ancient-job" in item for item in section.items)
    assert not any(item.startswith("jobs_24h") for item in section.items)


@pytest.mark.asyncio
async def test_an_empty_platform_omits_rather_than_inventing_a_section(tmp_db):
    """Nothing measurable is a legitimate outcome, not an error — and an empty
    section in a daily brief trains the reader to skip it."""
    store = SkillIndexStore(tmp_db)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert section.omitted
    assert section.items == []


@pytest.mark.asyncio
async def test_builtin_skills_do_not_inflate_the_used_ratio(tmp_db):
    """lifecycle_counts covers learned skills; the used-count must use the same
    population or the ratio compares two different denominators."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("learned-one", source="learned"))
    await store.upsert(_loaded("shipped", source="builtin"))

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert "skills_ever_used:0/1" in section.items


# --------------------------------------------------------------------------- #
# ADR-19 #4 — the lesson experiment reports itself.
# --------------------------------------------------------------------------- #


async def _outcome(
    db, trace: str, arm: str | None, quality: float | None, session: str = "chat-1",
) -> None:
    await db.execute(
        "INSERT INTO task_outcomes (trace_id, session_key, owl_name, channel, "
        "success, latency_ms, tool_call_count, captured_at, quality_score, "
        "lessons_arm, owner_id) "
        "VALUES (?, ?, 'o', 'cli', 1, 1.0, 0, 1.0, ?, ?, 'principal-default')",
        (trace, session, quality, arm),
    )


@pytest.mark.asyncio
async def test_both_arms_are_reported_once_both_have_data(tmp_db):
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "t1", "injected", 0.8)
    await _outcome(tmp_db, "t2", "injected", 0.6)
    await _outcome(tmp_db, "t3", "held_out", 0.4)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    line = next(i for i in section.items if i.startswith("lessons_quality"))
    assert "[interactive]" in line
    assert "injected:0.70(n=2)" in line
    assert "held_out:0.40(n=1)" in line


@pytest.mark.asyncio
async def test_the_COMPARABLE_metric_is_reported_beside_the_quality_one(tmp_db):
    """ESC-40. The quality line averages quality_score, which the critic scorer
    only ever writes for `success = 1` rows — so it compares SURVIVORS of a gate
    the treatment itself affects, and a collider makes the sign come out backwards.

    Measured 2026-08-24 over 6,890 arm-carrying rows: held_out succeeded 23.9% and
    injected 29.6% (z = -3.68), while the quality line said the opposite. Bakir's
    first brief in 14 days carried only the quality line and therefore told him
    withholding lessons produced better work.

    The success line is computed WITHOUT a quality_score filter, which is the whole
    point — filtering would reproduce the selection effect it exists to avoid.
    """
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "t1", "injected", 0.8)
    await _outcome(tmp_db, "t2", "injected", None)   # unscored — still counts here
    await _outcome(tmp_db, "t3", "held_out", 0.4)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    succ = next(i for i in section.items if i.startswith("lessons_success"))
    # n=2 for injected proves the UNSCORED row is counted: the quality line sees
    # one injected turn, this one must see both.
    assert "injected:100.0%(n=2)" in succ, succ
    assert "held_out:100.0%(n=1)" in succ, succ

    quality = next(i for i in section.items if i.startswith("lessons_quality"))
    assert "injected:0.80(n=1)" in quality, quality
    assert "success-gated" in quality, "the quality line must say what it is conditioned on"


@pytest.mark.asyncio
async def test_a_ONE_SIDED_result_is_not_reported(tmp_db):
    """A single arm is not a comparison. Printing it would invite a conclusion
    from noise — which is the failure mode this whole ADR is about."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "t1", "injected", 0.8)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert not any(i.startswith("lessons_quality") for i in section.items)


@pytest.mark.asyncio
async def test_unlabelled_and_unscored_turns_are_excluded(tmp_db):
    """A NULL arm means the turn is evidence for NEITHER side. Counting it as
    control would quietly bias the comparison toward 'lessons help'."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "t1", "injected", 0.8)
    await _outcome(tmp_db, "t2", "held_out", 0.4)
    await _outcome(tmp_db, "t3", None, 0.1)        # pre-experiment row
    await _outcome(tmp_db, "t4", "injected", None)  # never scored

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    line = next(i for i in section.items if i.startswith("lessons_quality"))
    assert "injected:0.80(n=1)" in line, line
    assert "held_out:0.40(n=1)" in line, line


@pytest.mark.asyncio
async def test_machine_and_interactive_lanes_are_reported_SEPARATELY(tmp_db):
    """Measured before shipping: 3,702 scored machine-lane turns against 329
    interactive ones, at very different baselines (0.52 vs 0.39). A single
    aggregate would be 92% background jobs, hiding the interactive answer — and
    a lane-mix difference between arms could flip the sign outright."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "i1", "injected", 0.40, session="chat-1")
    await _outcome(tmp_db, "i2", "held_out", 0.30, session="chat-2")
    await _outcome(tmp_db, "m1", "injected", 0.90, session="goal-goal_execution-x")
    await _outcome(tmp_db, "m2", "held_out", 0.80, session="incident-y")

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())
    lines = [i for i in section.items if i.startswith("lessons_quality")]

    assert len(lines) == 2, lines
    inter = next(i for i in lines if "[interactive]" in i)
    machine = next(i for i in lines if "[machine]" in i)
    assert "injected:0.40(n=1)" in inter and "held_out:0.30(n=1)" in inter
    assert "injected:0.90(n=1)" in machine and "held_out:0.80(n=1)" in machine


@pytest.mark.asyncio
async def test_a_lane_with_only_one_arm_is_skipped_while_the_other_reports(tmp_db):
    """Each lane is judged on its own evidence — a complete comparison must not
    be withheld because the other lane is one-sided."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "i1", "injected", 0.40, session="chat-1")
    await _outcome(tmp_db, "i2", "held_out", 0.30, session="chat-2")
    await _outcome(tmp_db, "m1", "injected", 0.90, session="goal-x")

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())
    lines = [i for i in section.items if i.startswith("lessons_quality")]

    assert len(lines) == 1
    assert "[interactive]" in lines[0]


# --------------------------------------------------------------------------- #
# Reply length — the prompt can only ASK; this is what checks.
# --------------------------------------------------------------------------- #


async def _sized(db, trace: str, chars: int | None, success: int = 1) -> None:
    import time as _t
    await db.execute(
        "INSERT INTO task_outcomes (trace_id, session_key, owl_name, channel, "
        "success, latency_ms, tool_call_count, captured_at, response_chars, owner_id) "
        "VALUES (?, 's', 'o', 'cli', ?, 1.0, 0, ?, ?, 'principal-default')",
        (trace, success, _t.time(), chars),
    )


@pytest.mark.asyncio
async def test_reply_length_counts_how_many_exceed_ONE_telegram_message(tmp_db):
    """The average alone hides the problem. What breaks markdown entities and
    the Like button is a reply crossing 4096 chars into a SPLIT, so the count
    over that line is the number worth watching."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _sized(tmp_db, "r1", 1000)
    await _sized(tmp_db, "r2", 9000)
    await _sized(tmp_db, "r3", 5000)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    line = next(i for i in section.items if i.startswith("reply_len"))
    assert "avg:5000" in line
    assert "over_4096:2/3" in line


@pytest.mark.asyncio
async def test_unmeasured_rows_are_excluded_rather_than_counted_as_zero(tmp_db):
    """response_chars is NULL on every row written before migration 0109.
    Counting those as 0 would drag the average down and invent an improvement
    that never happened."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _sized(tmp_db, "r1", 8000)
    await _sized(tmp_db, "r2", None)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    line = next(i for i in section.items if i.startswith("reply_len"))
    assert "avg:8000" in line, line
    assert "over_4096:1/1" in line, line


@pytest.mark.asyncio
async def test_no_measured_replies_means_no_line(tmp_db):
    """Before any turn runs there is nothing to say, and an empty metric in a
    daily brief teaches the reader to skip the section."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert not any(i.startswith("reply_len") for i in section.items)
