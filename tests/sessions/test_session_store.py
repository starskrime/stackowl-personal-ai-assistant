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
    # Both counters describe THIS run, not the lane's lifetime. message_count is
    # 1 because the message that crossed the boundary belongs to the new
    # incarnation; nothing has been answered on it yet.
    assert second.message_count == 1
    assert second.completed_turns == 0


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


# --------------------------------------------------------------------------
# D01.7 slice 3b — the rollover is announced.
#
# This is the seam D09.1 (background review), D09.3 (the curator) and Q17's
# memory summary all subscribe to, instead of each building its own idle
# detector. Dedup target X3, resolved architecturally.
# --------------------------------------------------------------------------


class _Bus:
    def __init__(self, boom: bool = False) -> None:
        self.events: list[tuple[str, object]] = []
        self._boom = boom

    def emit(self, event: str, payload: object = None) -> None:
        if self._boom:
            raise RuntimeError("subscriber exploded")
        self.events.append((event, payload))


@pytest.fixture
async def bus_store(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    from stackowl.db.migrations.runner import MigrationRunner
    MigrationRunner(tmp_path / "t.db").run()
    bus = _Bus()
    yield SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4),
                       mirror_dir=tmp_path, event_bus=bus), bus
    await db.close()


@pytest.mark.asyncio
async def test_a_rollover_is_published(bus_store) -> None:
    store, bus = bus_store
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    rolled, branch, reason = await store.resolve_for(src(), at(22, 12))

    assert branch is Branch.EXPIRED
    assert len(bus.events) == 1
    name, payload = bus.events[0]
    assert name == "session.rollover"
    assert payload["old_session_id"] == first.session_id
    assert payload["new_session_id"] == rolled.session_id
    assert payload["reason"] == reason.value
    assert payload["owl_name"] == "Brain"


@pytest.mark.asyncio
async def test_an_ordinary_turn_publishes_nothing(bus_store) -> None:
    """Only a BOUNDARY is an event. A normal turn is not."""
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    await store.resolve_for(src(), at(20, 13))
    assert bus.events == []


@pytest.mark.asyncio
async def test_a_first_message_is_not_a_rollover(bus_store) -> None:
    """A brand-new lane ended nothing, so there is nothing to announce."""
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    assert bus.events == []


@pytest.mark.asyncio
async def test_a_throwing_subscriber_never_blocks_the_conversation(tmp_path,
                                                                   monkeypatch) -> None:
    """A rollover fires at 4 AM unattended. A broken consumer must not be able to
    stop the user's next conversation from starting."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    db = DbPool(db_path=tmp_path / "t.db")
    await db.open()
    from stackowl.db.migrations.runner import MigrationRunner
    MigrationRunner(tmp_path / "t.db").run()
    store = SessionStore(db, ResetPolicy(mode=ResetMode.BOTH, at_hour=4),
                         mirror_dir=tmp_path, event_bus=_Bus(boom=True))
    try:
        await store.resolve_for(src(), at(20, 12))
        rolled, branch, _ = await store.resolve_for(src(), at(22, 12))
        assert branch is Branch.EXPIRED
        assert rolled.session_id  # the new incarnation exists regardless
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_bus_is_a_supported_configuration(store) -> None:
    """The default store has no bus; the boundary is still logged and still happens."""
    await store.resolve_for(src(), at(20, 12))
    rolled, branch, _ = await store.resolve_for(src(), at(22, 12))
    assert branch is Branch.EXPIRED
    assert rolled.session_id


# --------------------------------------------------------------------------
# D01.7 slice 3b — /new: an explicit boundary, sharing the automatic one's path.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_ends_the_incarnation_and_starts_another(bus_store) -> None:
    store, bus = bus_store
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    fresh = await store.start_new_incarnation(first.session_key)

    assert fresh is not None
    assert fresh.session_key == first.session_key, "same lane (I1)"
    assert fresh.session_id != first.session_id, "new incarnation (I2)"
    # /new starts an empty run: unlike an automatic boundary, no message crossed
    # into it — the next one to arrive will be its first.
    assert fresh.message_count == 0
    assert fresh.completed_turns == 0


@pytest.mark.asyncio
async def test_an_explicit_new_never_reports_itself_as_an_expiry(bus_store) -> None:
    """The user did this on purpose. Telling them their conversation 'went quiet'
    would be a lie — which is why is_fresh_reset is kept distinct from
    was_auto_reset."""
    from stackowl.sessions.policy import reset_notice

    store, _ = bus_store
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    fresh = await store.start_new_incarnation(first.session_key)

    assert fresh is not None
    assert fresh.is_fresh_reset is True
    assert fresh.was_auto_reset is False
    assert reset_notice(fresh) is None, "no 'expired' notice for a deliberate /new"


@pytest.mark.asyncio
async def test_new_announces_the_boundary_like_any_other(bus_store) -> None:
    """One code path, four triggers — a consumer cannot tell (or need to tell)
    an explicit boundary from a daily one except by the reason field."""
    store, bus = bus_store
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    await store.start_new_incarnation(first.session_key)

    assert len(bus.events) == 1
    name, payload = bus.events[0]
    assert name == "session.rollover"
    assert payload["reason"] == "explicit"
    assert payload["old_session_id"] == first.session_id


@pytest.mark.asyncio
async def test_new_on_an_unknown_lane_reports_nothing_happened(bus_store) -> None:
    """Nothing to end. Inventing a lane would announce a rollover that never was."""
    store, bus = bus_store
    assert await store.start_new_incarnation("owl:Brain:telegram:dm:nope") is None
    assert bus.events == []


# --------------------------------------------------------------------------
# D01.7 slice 3b — the sweeper: a boundary is a CLOCK event, not a traffic event.
#
# Without it, "4 AM" really means "whenever you next say something", and Q17's
# overnight summary never runs unattended — which is precisely when nobody is
# watching.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sweeper_finalises_an_expired_lane(bus_store) -> None:
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    finalized, skipped = await store.sweep(at(22, 12))

    assert (finalized, skipped) == (1, 0)
    assert len(bus.events) == 1
    assert bus.events[0][0] == "session.rollover"


@pytest.mark.asyncio
async def test_the_sweeper_leaves_a_live_lane_alone(bus_store) -> None:
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    assert await store.sweep(at(20, 13)) == (0, 0)
    assert bus.events == []


@pytest.mark.asyncio
async def test_i4_a_busy_lane_is_never_expired(bus_store) -> None:
    """Bakir's Q12, extended beyond the reference platform: an agent working overnight must not
    have its conversation cut from under it."""
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))

    async def always_busy(entry) -> bool:  # noqa: ANN001
        return True

    finalized, skipped = await store.sweep(at(22, 12), is_busy=always_busy)
    assert (finalized, skipped) == (0, 1)
    assert bus.events == [], "a busy lane announces nothing"


@pytest.mark.asyncio
async def test_a_busy_lane_is_retried_on_the_next_sweep(bus_store) -> None:
    """Skipped, not dropped: the work finishes and the boundary still happens."""
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    busy = {"value": True}

    async def is_busy(entry) -> bool:  # noqa: ANN001
        return busy["value"]

    assert await store.sweep(at(22, 12), is_busy=is_busy) == (0, 1)
    busy["value"] = False
    assert await store.sweep(at(22, 13), is_busy=is_busy) == (1, 0)


@pytest.mark.asyncio
async def test_a_lane_is_only_finalised_once(bus_store) -> None:
    store, bus = bus_store
    await store.resolve_for(src(), at(20, 12))
    await store.sweep(at(22, 12))
    assert await store.sweep(at(22, 13)) == (0, 0), "expiry_finalized is honoured"
    assert len(bus.events) == 1


@pytest.mark.asyncio
async def test_a_swept_lane_is_not_announced_twice(bus_store) -> None:
    """The sweeper announced the boundary on the clock. When the user finally
    speaks — possibly hours later — the same boundary must not fire every
    consumer a second time."""
    store, bus = bus_store
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    await store.sweep(at(22, 12))
    assert len(bus.events) == 1

    rolled, branch, _ = await store.resolve_for(src(), at(22, 14))
    assert branch is Branch.EXPIRED
    assert rolled.session_id != first.session_id, "the new incarnation is still minted"
    assert len(bus.events) == 1, "but the boundary is announced only once"


@pytest.mark.asyncio
async def test_the_sweeper_skips_a_lane_awaiting_recovery(bus_store) -> None:
    """resume_pending means a turn still needs finishing — expiring it would
    discard the very turn we are recovering."""
    store, bus = bus_store
    entry, _, _ = await store.resolve_for(src(), at(20, 12))
    await store.save(entry.evolve(resume_pending=True))
    assert await store.sweep(at(22, 12)) == (0, 0)
    assert bus.events == []


# ---------------------------------------------------------------------------
# D01.7 slice 3b part 4 — the lane row tells the truth.
#
# turn_count counted NOTHING: minted at 0, reset to 0, persisted, read back and
# published in the rollover payload, with no code path ever incrementing it. The
# live lane read 0 against a 4-message transcript. These tests pin the two
# counters that replace it, and the identity the lane must carry so a summary is
# filed where recall looks.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_count_rises_with_every_inbound_message(store) -> None:
    """The defect, stated as a test: a counter that never counts is a lie."""
    first, _, _ = await store.resolve_for(src(), at(20, 12))
    assert first.message_count == 1
    second, _, _ = await store.resolve_for(src(), at(20, 13))
    assert second.message_count == 2
    third, _, _ = await store.resolve_for(src(), at(20, 14))
    assert third.message_count == 3


@pytest.mark.asyncio
async def test_message_count_survives_the_round_trip(store) -> None:
    """Persisted, not merely held — the old field round-tripped a constant."""
    await store.resolve_for(src(), at(20, 12))
    await store.resolve_for(src(), at(20, 13))
    reloaded = await store.get(build_key())
    assert reloaded is not None
    assert reloaded.message_count == 2


@pytest.mark.asyncio
async def test_a_rollover_resets_both_counters(store) -> None:
    """A new incarnation counts its OWN turns, not the lane's lifetime."""
    await store.resolve_for(src(), at(20, 22))
    await store.record_completed_turn(build_key())
    rolled, branch, _ = await store.resolve_for(src(), at(21, 9))
    assert branch is Branch.EXPIRED
    assert rolled.message_count == 1      # this message started the new run
    assert rolled.completed_turns == 0


@pytest.mark.asyncio
async def test_completed_turns_counts_replies_not_messages(store) -> None:
    """The difference between the two counters is the health signal."""
    await store.resolve_for(src(), at(20, 12))
    await store.resolve_for(src(), at(20, 13))
    await store.record_completed_turn(build_key())
    reloaded = await store.get(build_key())
    assert reloaded is not None
    assert reloaded.message_count == 2
    assert reloaded.completed_turns == 1


@pytest.mark.asyncio
async def test_record_completed_turn_on_an_unknown_lane_is_a_no_op(store) -> None:
    """Never invent a lane from a turn-end hook. It runs on background work too."""
    await store.record_completed_turn("owl:Nobody:cli:dm:nope")
    assert await store.get("owl:Nobody:cli:dm:nope") is None


@pytest.mark.asyncio
async def test_the_lane_records_who_it_belongs_to(store) -> None:
    """identity_key is what makes a rollover summary reachable by recall.

    Facts are filed under the PERSON. A summary filed under the owl-prefixed lane
    is one recall never sees — the same defect fixed in turn_persist, which this
    column exists to stop recurring at the boundary.
    """
    source = SessionSource("Brain", "telegram", ChatType.DM, "123",
                           identity_key="bakir")
    entry, _, _ = await store.resolve_for(source, at(20, 12))
    assert entry.identity_key == "bakir"
    reloaded = await store.get(entry.session_key)
    assert reloaded is not None
    assert reloaded.identity_key == "bakir"


@pytest.mark.asyncio
async def test_a_message_without_an_identity_never_erases_a_known_one(store) -> None:
    """Same rule as chat_target: the newest message wins, but silence does not.

    A CLI turn that cannot state an identity must not unlink the lane from its
    owner, or the next rollover summary is filed nowhere.
    """
    known = SessionSource("Brain", "telegram", ChatType.DM, "123",
                          identity_key="bakir")
    await store.resolve_for(known, at(20, 12))
    anonymous = SessionSource("Brain", "telegram", ChatType.DM, "123")
    entry, _, _ = await store.resolve_for(anonymous, at(20, 13))
    assert entry.identity_key == "bakir"


@pytest.mark.asyncio
async def test_the_rollover_payload_carries_identity_and_both_counters(store) -> None:
    """A consumer must not have to re-read the store to enqueue durable work.

    The payload previously published turn_count, which was always 0 — a consumer
    gating on it read a constant.
    """
    seen: list[dict] = []

    class _Bus:
        def emit(self, event: str, payload: dict) -> None:
            seen.append(payload)

    store._event_bus = _Bus()
    source = SessionSource("Brain", "telegram", ChatType.DM, "123",
                           identity_key="bakir")
    await store.resolve_for(source, at(20, 22))
    await store.record_completed_turn(build_key())
    await store.resolve_for(source, at(21, 9))

    assert len(seen) == 1
    payload = seen[0]
    assert payload["identity_key"] == "bakir"
    assert payload["message_count"] == 1
    assert payload["completed_turns"] == 1
    assert "turn_count" not in payload


def build_key() -> str:
    from stackowl.sessions.models import build_session_key
    return build_session_key(src())
