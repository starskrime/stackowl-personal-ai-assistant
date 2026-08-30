"""D01.7 slice 3b part 7b — runner knowledge follows the PERSON (DEBT-13).

THE DECISION THIS ENCODES. `owner_scope_key` is ``identity_key or session_key``,
and none of the four runner modules sets an identity — so each files its durable
knowledge under an ad-hoc lane string ("objective-abc", "session:label",
"recover-xxx", "shadow-validate-xxx"). Giving them composite lanes would silently
repoint where those facts are staged and read, which is precisely the bug class the
`3a.2` addendum ruled on.

Bakir's answer: a runner INHERITS its identity from the conversation that asked.
Knowledge is about a person, not about which machinery happened to produce it, so
an objective started from a chat stages its facts where that chat's recall already
looks. An orphan runner — a cron job nobody asked for — has no person, and its lane
stays its scope.

This is why part 7a had to land first: `parent_session_key` is what makes the
parent's identity reachable at all.
"""

from __future__ import annotations

import datetime

import pytest
from tests._schema_template import seed_schema

from stackowl.db.pool import DbPool
from stackowl.sessions import ChatType, ResetMode, ResetPolicy, SessionSource
from stackowl.sessions.store import SessionStore

UTC = datetime.UTC


def at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = DbPool(db_path=tmp_path / "test.db")
    await db.open()
    seed_schema(tmp_path / "test.db")
    yield SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4),
                       mirror_dir=tmp_path)
    await db.close()


async def _parent_chat(store: SessionStore, identity: str = "bakir") -> str:
    entry, _, _ = await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123",
                      identity_key=identity),
        at(20, 10),
    )
    return entry.session_key


@pytest.mark.asyncio
async def test_a_runner_inherits_the_identity_of_the_conversation_that_asked(
    store: SessionStore,
) -> None:
    """THE HEADLINE. An objective's facts must land where the asking chat's recall
    looks, not under a lane nobody queries."""
    parent = await _parent_chat(store)
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc", parent_session_key=parent),
        at(20, 12),
    )
    assert entry.identity_key == "bakir"


@pytest.mark.asyncio
async def test_the_inherited_identity_is_persisted_not_merely_returned(
    store: SessionStore,
) -> None:
    """The 4 AM sweeper reads it off the row; a value that only existed in memory
    would leave the summary unattributable."""
    parent = await _parent_chat(store)
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc", parent_session_key=parent),
        at(20, 12),
    )
    reloaded = await store.get(entry.session_key)
    assert reloaded is not None
    assert reloaded.identity_key == "bakir"


@pytest.mark.asyncio
async def test_an_explicit_identity_beats_the_parents(store: SessionStore) -> None:
    """Inheritance fills a gap; it never overrides something the caller knows."""
    parent = await _parent_chat(store)
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc", parent_session_key=parent,
                      identity_key="someone-else"),
        at(20, 12),
    )
    assert entry.identity_key == "someone-else"


@pytest.mark.asyncio
async def test_an_orphan_runner_has_no_identity_to_inherit(
    store: SessionStore,
) -> None:
    """A cron job nobody asked for has no person behind it. None is honest; a
    fabricated owner would misattribute somebody's memory."""
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="cron",
                      runner_id="health_sweep"),
        at(20, 12),
    )
    assert entry.identity_key is None


@pytest.mark.asyncio
async def test_a_parent_that_does_not_exist_is_not_an_error(
    store: SessionStore,
) -> None:
    """A runner may outlive the lane that spawned it, or name one that was pruned.
    That degrades to no identity — it never raises on the work's critical path."""
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc",
                      parent_session_key="owl:Brain:telegram:dm:gone"),
        at(20, 12),
    )
    assert entry.identity_key is None


@pytest.mark.asyncio
async def test_a_parent_with_no_identity_yields_none(store: SessionStore) -> None:
    """Inheriting from an anonymous lane must not invent one."""
    anon, _, _ = await store.resolve_for(
        SessionSource("Brain", "cli", ChatType.DM, "local"), at(20, 10),
    )
    entry, _, _ = await store.resolve_for(
        SessionSource(owl_name="Brain", channel="cron", runner="objective",
                      runner_id="abc", parent_session_key=anon.session_key),
        at(20, 12),
    )
    assert entry.identity_key is None


@pytest.mark.asyncio
async def test_a_chat_lane_never_inherits_from_anything(store: SessionStore) -> None:
    """Inheritance is a RUNNER rule. A chat lane's identity comes from its own
    ingress, and must not be reachable through a parent it should never have."""
    parent = await _parent_chat(store)
    entry, _, _ = await store.resolve_for(
        SessionSource("Scout", "telegram", ChatType.DM, "999",
                      parent_session_key=parent),
        at(20, 12),
    )
    assert entry.identity_key is None


# ------------------------------------------------------- the call-site helper


@pytest.mark.asyncio
async def test_the_helper_returns_the_lane_its_incarnation_and_identity(
    store: SessionStore, monkeypatch,
) -> None:
    from stackowl.pipeline import services as services_mod

    parent = await _parent_chat(store)

    class _Svc:
        session_store = store

    monkeypatch.setattr(services_mod, "get_services", lambda: _Svc())
    lane, incarnation, identity = await services_mod.resolve_runner_lane(
        runner="objective", runner_id="abc", owl_name="Brain", channel="cron",
        parent_session_key=parent, fallback="objective-abc",
    )
    assert lane == "owl:Brain:objective:abc"
    assert incarnation
    assert identity == "bakir"


@pytest.mark.asyncio
async def test_the_helper_degrades_to_todays_behaviour_with_no_store(
    monkeypatch,
) -> None:
    """No store wired (CLI, dry runs, tests) must behave EXACTLY as before this
    slice: the ad-hoc key, no incarnation, no identity. A lane is an enhancement to
    background work, never a precondition for it running."""
    from stackowl.pipeline import services as services_mod

    class _Svc:
        session_store = None

    monkeypatch.setattr(services_mod, "get_services", lambda: _Svc())
    assert await services_mod.resolve_runner_lane(
        runner="objective", runner_id="abc", owl_name="Brain", channel="cron",
        fallback="objective-abc",
    ) == ("objective-abc", "", None)


@pytest.mark.asyncio
async def test_a_failing_store_never_breaks_the_runner(monkeypatch) -> None:
    """The work matters more than its lane. A store error degrades to the fallback,
    loudly logged, rather than aborting an objective."""
    from stackowl.pipeline import services as services_mod

    class _Broken:
        async def resolve_for(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("db gone")

    class _Svc:
        session_store = _Broken()

    monkeypatch.setattr(services_mod, "get_services", lambda: _Svc())
    assert await services_mod.resolve_runner_lane(
        runner="objective", runner_id="abc", owl_name="Brain", channel="cron",
        fallback="objective-abc",
    ) == ("objective-abc", "", None)
