"""D01.7 slice 3b part 7 — every runner gets a lane (Q9).

Q9 said "EVERY runner gets a lane — cron jobs, delegated subagents, objectives.
One rule, no special cases." It was never implemented: `resolve_for` had exactly
ONE caller, the chat ingress, so background work had no lane, no incarnation, no
frozen prompt and no boundary.

TWO DELIBERATE DIVERGENCES FROM HERMES, both recorded in the design doc:

* Hermes gives non-chat work NO lane — delegated runs inherit the ambient key and
  cron is an external service delivering into a chat. We give autonomous work its
  OWN lane so it earns its own frozen prompt (the D01.1 win their model forfeits),
  and link it to the conversation that asked via `parent_session_key` so the story
  stays whole.
* Cron follows Hermes: it runs on the lane of the chat it delivers to, and only
  falls back to a synthetic job lane when there is no single target. That is
  deliberately TWO rules where Q9 said one — cron is a delivery, an objective is
  work — and it is written down so nobody "fixes" it into one.
"""

from __future__ import annotations

import datetime

import pytest

from stackowl.db.pool import DbPool
from stackowl.sessions import ChatType, ResetMode, ResetPolicy, SessionSource
from stackowl.sessions.models import Branch, build_session_key
from stackowl.sessions.store import SessionStore

UTC = datetime.UTC


def at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = DbPool(db_path=tmp_path / "test.db")
    await db.open()
    from stackowl.db.migrations.runner import MigrationRunner
    MigrationRunner(tmp_path / "test.db").run()
    yield SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4),
                       mirror_dir=tmp_path)
    await db.close()


# ------------------------------------------------------------------ the key


def test_a_runner_lane_is_keyed_by_what_it_is_and_which_one() -> None:
    key = build_session_key(SessionSource(
        owl_name="Brain", channel="cron", runner="objective", runner_id="715f6143",
    ))
    assert key == "owl:Brain:objective:715f6143"


def test_a_runner_lane_ignores_chat_isolation_settings() -> None:
    """Chat isolation asks 'whose messages are these'. A runner has no participants,
    so the group/thread rules must not silently reshape its key."""
    source = SessionSource(
        owl_name="Brain", channel="cron", runner="cron", runner_id="morning_brief",
        chat_type=ChatType.GROUP, participant_id="someone",
    )
    assert build_session_key(source, group_per_user=True) == \
        build_session_key(source, group_per_user=False) == \
        "owl:Brain:cron:morning_brief"


def test_two_runs_of_the_same_runner_share_one_lane() -> None:
    """I1 for runners: a daily brief is ONE conversation that rolls, not a new
    conversation every morning. Per-run lanes would rebuild the frozen prompt every
    time and lose exactly the D01.1 win this divergence was taken for."""
    def key(run: str) -> str:
        return build_session_key(SessionSource(
            owl_name="Brain", channel="cron", runner="cron",
            runner_id="morning_brief",
        ))
    assert key("run-1") == key("run-2")


def test_a_chat_source_is_unaffected() -> None:
    """The runner branch must not disturb the chat key format — 61k historical rows
    and every live lane depend on it."""
    assert build_session_key(SessionSource("Brain", "telegram", ChatType.DM, "123")) \
        == "owl:Brain:telegram:dm:123"


# ------------------------------------------------------------- the parent link


@pytest.mark.asyncio
async def test_a_runner_lane_remembers_the_conversation_that_asked(
    store: SessionStore,
) -> None:
    """Q19's reconciliation: own lane AND one story. The summary is attributed to
    the parent, so an objective started from a chat does not fragment it."""
    parent = "owl:Brain:telegram:dm:123"
    entry, branch, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc", parent_session_key=parent),
        at(20, 12),
    )
    assert branch is Branch.NEW
    assert entry.parent_session_key == parent
    reloaded = await store.get(entry.session_key)
    assert reloaded is not None
    assert reloaded.parent_session_key == parent


@pytest.mark.asyncio
async def test_an_orphan_runner_has_no_parent(store: SessionStore) -> None:
    """A cron job nobody asked for has no originating conversation. None is the
    honest answer; inventing one would misattribute its summary."""
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="cron",
                      runner_id="health_sweep"),
        at(20, 12),
    )
    assert entry.parent_session_key is None


@pytest.mark.asyncio
async def test_a_later_run_never_erases_a_known_parent(store: SessionStore) -> None:
    """Same COALESCE rule as chat_id and identity_key, for the same reason: a run
    that cannot state its parent must not orphan the lane."""
    parent = "owl:Brain:telegram:dm:123"
    src = SessionSource(owl_name="Brain", channel="cron", runner="objective",
                        runner_id="abc", parent_session_key=parent)
    await store.resolve_for(src, at(20, 12))
    orphan = SessionSource(owl_name="Brain", channel="cron", runner="objective",
                           runner_id="abc")
    entry, _, _ = await store.resolve_for(orphan, at(20, 13))
    assert entry.parent_session_key == parent


# ------------------------------------------------- runners obey the same policy


@pytest.mark.asyncio
async def test_a_runner_lane_rolls_on_the_same_daily_boundary(
    store: SessionStore,
) -> None:
    """Q9's answer to the reset policy: the SAME policy, with I4 protecting live
    work. One policy, no special cases."""
    src = SessionSource(owl_name="Brain", channel="cron", runner="cron",
                        runner_id="morning_brief")
    first, _, _ = await store.resolve_for(src, at(20, 22))
    second, branch, _ = await store.resolve_for(src, at(21, 9))
    assert branch is Branch.EXPIRED
    assert second.session_key == first.session_key      # same lane (I1)
    assert second.session_id != first.session_id        # new incarnation (I2)


@pytest.mark.asyncio
async def test_a_busy_runner_lane_is_never_expired(store: SessionStore) -> None:
    """I4 for runners is the point of part 6: an objective running overnight must
    not have its conversation cut."""
    src = SessionSource(owl_name="Brain", channel="cron", runner="objective",
                        runner_id="abc")
    await store.resolve_for(src, at(20, 22))

    async def _busy(entry) -> bool:  # noqa: ANN001
        return True

    finalized, skipped = await store.sweep(now=at(21, 9), is_busy=_busy)
    assert (finalized, skipped) == (0, 1)
