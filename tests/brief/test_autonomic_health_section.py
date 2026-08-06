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

    line = next(i for i in section.items if i.startswith("lessons_effect"))
    assert "[interactive]" in line
    assert "injected:0.70(n=2)" in line
    assert "held_out:0.40(n=1)" in line


@pytest.mark.asyncio
async def test_a_ONE_SIDED_result_is_not_reported(tmp_db):
    """A single arm is not a comparison. Printing it would invite a conclusion
    from noise — which is the failure mode this whole ADR is about."""
    store = SkillIndexStore(tmp_db)
    await store.upsert(_loaded("a"))
    await _outcome(tmp_db, "t1", "injected", 0.8)

    section = await AutonomicHealthAssembler(store, tmp_db).assemble(_ctx())

    assert not any(i.startswith("lessons_effect") for i in section.items)


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

    line = next(i for i in section.items if i.startswith("lessons_effect"))
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
    lines = [i for i in section.items if i.startswith("lessons_effect")]

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
    lines = [i for i in section.items if i.startswith("lessons_effect")]

    assert len(lines) == 1
    assert "[interactive]" in lines[0]
