"""D01.7 slice 2 — SessionStore against a real SQLite database.

Real DbPool, real migrations, a temp HOME. No mocks: this is the layer where a
mock would hide exactly the bugs that matter (column drift, boolean round-trips,
upsert semantics).
"""

from __future__ import annotations

import datetime
import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.sessions import ChatType, ResetMode, ResetPolicy, SessionSource
from stackowl.sessions.models import Branch, ResetReason
from stackowl.sessions.store import SessionStore

UTC = datetime.UTC


def at(day: int, hour: int) -> datetime.datetime:
    return datetime.datetime(2026, 7, day, hour, tzinfo=UTC)


def src(owl: str = "Brain", chat: str = "123") -> SessionSource:
    return SessionSource(owl, "telegram", ChatType.DM, chat)


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


@pytest.mark.asyncio
async def test_first_message_creates_a_lane(store) -> None:
    entry, branch, reason = await store.resolve_for(src(), at(20, 12))
    assert branch is Branch.NEW
    assert reason is None
    assert entry.session_key.startswith("owl:Brain:telegram:dm:")
    assert entry.session_id


@pytest.mark.asyncio
async def test_i1_the_lane_survives_a_round_trip_unchanged(store) -> None:
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    second, branch, _ = await store.resolve_for(src(), at(20, 13))
    assert second.session_key == first.session_key
    assert second.session_id == first.session_id
    assert branch is Branch.EXISTING


@pytest.mark.asyncio
async def test_i2_a_rollover_keeps_the_key_and_mints_a_new_id(store) -> None:
    first, _, _ = await store.resolve_for(src(), at(20, 22))
    second, branch, reason = await store.resolve_for(src(), at(21, 9))
    assert branch is Branch.EXPIRED
    assert reason is ResetReason.DAILY
    assert second.session_key == first.session_key   # lane unchanged (I1)
    assert second.session_id != first.session_id     # incarnation new (I2)
    assert second.was_auto_reset is True
    assert second.turn_count == 0


@pytest.mark.asyncio
async def test_i5_the_notice_is_consumed_exactly_once(store) -> None:
    await store.resolve_for(src(), at(20, 22))
    rolled, _, _ = await store.resolve_for(src(), at(21, 9))
    assert rolled.was_auto_reset is True
    cleared = await store.consume_reset_notice(rolled)
    assert cleared.was_auto_reset is False
    assert (await store.get(rolled.session_key)).was_auto_reset is False


@pytest.mark.asyncio
async def test_resume_pending_preserves_the_incarnation(store) -> None:
    """The case StackOwl hits constantly: the core exec-replaced mid-turn."""
    first, _, _ = await store.resolve_for(src(), at(20, 22))
    await store.save(first.evolve(resume_pending=True, resume_reason="restart"))
    resumed, branch, _ = await store.resolve_for(src(), at(21, 9))
    assert branch is Branch.RESUME
    assert resumed.session_id == first.session_id   # transcript continues
    await store.clear_resume_pending(first.session_key)
    assert (await store.get(first.session_key)).resume_pending is False


@pytest.mark.asyncio
async def test_i4_active_work_blocks_a_due_rollover(store) -> None:
    first, _, _ = await store.resolve_for(src(), at(20, 22))
    kept, branch, _ = await store.resolve_for(src(), at(21, 9), has_active_work=True)
    assert branch is Branch.EXISTING
    assert kept.session_id == first.session_id


@pytest.mark.asyncio
async def test_flags_survive_the_sqlite_boolean_round_trip(store) -> None:
    entry, _, _ = await store.resolve_for(src(), at(20, 12))
    await store.save(entry.evolve(suspended=True, is_fresh_reset=True,
                                  restart_failures=2,
                                  auto_reset_reason=ResetReason.IDLE))
    back = await store.get(entry.session_key)
    assert back.suspended is True
    assert back.is_fresh_reset is True
    assert back.restart_failures == 2
    assert back.auto_reset_reason is ResetReason.IDLE


@pytest.mark.asyncio
async def test_different_owls_are_different_lanes(store) -> None:
    a, _, _ = await store.resolve_for(src(owl="Brain"), at(20, 12))
    b, _, _ = await store.resolve_for(src(owl="Scout"), at(20, 12))
    assert a.session_key != b.session_key
    assert len(await store.list_all()) == 2


@pytest.mark.asyncio
async def test_prune_drops_records_but_never_recovery_state(store) -> None:
    old, _, _ = await store.resolve_for(src(chat="old"), at(1, 12))
    recovering, _, _ = await store.resolve_for(src(chat="rec"), at(1, 12))
    await store.save(recovering.evolve(resume_pending=True))
    pruned = await store.prune(at(20, 12))
    assert pruned == 1
    assert await store.get(old.session_key) is None
    assert await store.get(recovering.session_key) is not None


# ---------------------------------------------------------------------------
# The mirror is DERIVED and WRITE-ONLY. These tests pin that asymmetry.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mirror_projects_the_key_to_id_map(store) -> None:
    entry, _, _ = await store.resolve_for(src(), at(20, 12))
    data = json.loads(store.mirror_path().read_text(encoding="utf-8"))
    assert entry.session_key in data["lanes"]
    assert data["lanes"][entry.session_key]["session_id"] == entry.session_id
    assert "WRITE-ONLY" in data["_note"]


@pytest.mark.asyncio
async def test_a_corrupted_mirror_cannot_affect_behaviour(store) -> None:
    """The whole justification for accepting dual storage: nothing reads it, so a
    hand-edited or corrupted mirror is regenerated and never believed."""
    entry, _, _ = await store.resolve_for(src(), at(20, 12))
    store.mirror_path().write_text("{ not json at all", encoding="utf-8")
    again, branch, _ = await store.resolve_for(src(), at(20, 13))
    assert branch is Branch.EXISTING
    assert again.session_id == entry.session_id
    assert json.loads(store.mirror_path().read_text(encoding="utf-8"))["lanes"]


@pytest.mark.asyncio
async def test_a_deleted_mirror_regenerates(store) -> None:
    await store.resolve_for(src(), at(20, 12))
    store.mirror_path().unlink()
    await store.resolve_for(src(), at(20, 13))
    assert store.mirror_path().exists()


# --------------------------------------------------------------------------
# D01.7 slice 3a.2 — the native send target lives ON the lane.
#
# Once a lane is a composite key ("owl:Brain:telegram:dm:123") it is no longer
# int()-able into a chat id, and channels/base.py resolve_target used to rely on
# exactly that. Bakir's choice was to store the target rather than parse the key,
# because on Slack the lane was never the send target in the first place.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_native_send_target_is_persisted_on_the_lane(store) -> None:
    entry, _, _ = await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="456"),
        at(20, 12),
    )
    assert entry.chat_id == "456"
    reloaded = await store.get(entry.session_key)
    assert reloaded is not None
    assert reloaded.chat_id == "456"


@pytest.mark.asyncio
async def test_the_send_target_is_resolvable_from_the_lane_alone(store) -> None:
    """What proactive delivery actually needs: lane in, native target out."""
    source = SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="456")
    entry, _, _ = await store.resolve_for(source, at(20, 12))
    assert await store.resolve_send_target(entry.session_key) == "456"


@pytest.mark.asyncio
async def test_an_unknown_lane_resolves_to_no_target(store) -> None:
    """Never fabricate a recipient — a fabricated chat id IS the cross-deliver bug."""
    assert await store.resolve_send_target("owl:Brain:telegram:dm:nope") is None


@pytest.mark.asyncio
async def test_a_rollover_keeps_the_send_target(store) -> None:
    """A new incarnation is still the same lane, so it is still the same recipient."""
    source = SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="456")
    first, _, _ = await store.resolve_for(source, at(20, 12))
    rolled, branch, _ = await store.resolve_for(source, at(22, 12))
    assert branch is Branch.EXPIRED
    assert rolled.session_id != first.session_id
    assert rolled.chat_id == "456"


@pytest.mark.asyncio
async def test_a_later_message_refreshes_a_changed_send_target(store) -> None:
    """Telegram can re-key a chat (a group upgraded to a supergroup). The lane is
    unchanged, so the stored target must follow the newest message, not the first."""
    entry, _, _ = await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="456"), at(20, 12))
    moved, _, _ = await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="789"), at(20, 13))
    assert moved.session_key == entry.session_key
    assert moved.chat_id == "789"


@pytest.mark.asyncio
async def test_a_message_without_a_target_does_not_erase_the_known_one(store) -> None:
    """A channel that cannot state its target (CLI) must not blank a real one."""
    await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123", chat_target="456"), at(20, 12))
    later, _, _ = await store.resolve_for(
        SessionSource("Brain", "telegram", ChatType.DM, "123"), at(20, 13))
    assert later.chat_id == "456"
