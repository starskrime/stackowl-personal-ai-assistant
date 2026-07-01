"""PB0b — cross-process telegram receive-liveness (RC0 regression gate).

RC0: the telegram receive loop died and stayed dead 30h while the health sweep
reported ok. These witnesses prove the durable liveness signal survives across
DB connections (the cross-process property) and that a STALE stamp is reported
degraded — the assertion that would have caught the 30-hour outage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stackowl.channels.liveness import ChannelLivenessStore
from stackowl.channels.telegram.adapter import TelegramChannelAdapter
from stackowl.channels.telegram.settings import TelegramSettings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.health.contributors import ChannelLivenessContributor


class FakeClock:
    """Wall clock the test drives; ``now()`` returns a tz-aware UTC datetime."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

    def monotonic(self) -> float:  # pragma: no cover - unused by these tests
        return 0.0


async def _migrated_pool(tmp_path: Path) -> DbPool:
    db_path = tmp_path / "live.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path)
    await pool.open()
    return pool


# 1. Store round-trip -------------------------------------------------------
async def test_store_roundtrip(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram")
        got = await store.read_last_receive_at("telegram")
        assert got == clock.now()
        assert await store.read_last_receive_at("slack") is None
    finally:
        await pool.close()


# 2. Contributor fresh -> ok ------------------------------------------------
async def test_contributor_fresh_is_ok(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram")  # stamped at T
        clock.advance(10)  # read at T+10s
        contrib = ChannelLivenessContributor(store, "telegram", clock)
        status = await contrib.health_check()
        assert status.status == "ok"
    finally:
        await pool.close()


# 3. Contributor stale -> degraded (THE RC0 WITNESS) ------------------------
async def test_contributor_stale_is_degraded(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram")  # stamped at T
        clock.advance(300)  # T+300s, well past STALE_AFTER_S=120
        contrib = ChannelLivenessContributor(store, "telegram", clock)
        status = await contrib.health_check()
        assert status.status == "degraded"
        assert "stale" in (status.message or "")
    finally:
        await pool.close()


# 4. Contributor no row -> down ---------------------------------------------
async def test_contributor_no_row_is_down(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        contrib = ChannelLivenessContributor(store, "telegram", clock)
        status = await contrib.health_check()
        assert status.status == "down"
    finally:
        await pool.close()


# 4b. PB-CANARY regression pin — the default (kind="receive") construction is
# BYTE-IDENTICAL to before the generalization: same contributor_name, same
# down/degraded/ok message text. This is the single most important
# backward-compat check for the generalization (must never drift).
async def test_default_kind_is_byte_identical_to_pre_generalization(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        contrib = ChannelLivenessContributor(store, "telegram", clock)
        assert contrib.contributor_name == "telegram_receive"

        # down (no row)
        status = await contrib.health_check()
        assert status.status == "down"
        assert status.message == "telegram receive loop never reported alive"

        # ok (fresh)
        await store.mark_alive("telegram")
        clock.advance(10)
        status = await contrib.health_check()
        assert status.status == "ok"
        assert status.message == "last update 10s ago"

        # degraded (stale)
        clock.advance(300)
        status = await contrib.health_check()
        assert status.status == "degraded"
        assert status.message == "telegram receive loop stale — last update 310s ago"
    finally:
        await pool.close()


# 5. Cross-connection realism (the cross-process property) ------------------
async def test_signal_survives_across_separate_instances(tmp_path: Path) -> None:
    clock = FakeClock()
    db_path = tmp_path / "live.db"
    MigrationRunner(db_path=db_path).run()
    writer_pool = DbPool(db_path)
    reader_pool = DbPool(db_path)
    await writer_pool.open()
    await reader_pool.open()
    try:
        writer = ChannelLivenessStore(writer_pool, clock)
        reader = ChannelLivenessStore(reader_pool, clock)
        await writer.mark_alive("telegram")
        # A SEPARATE instance on a SEPARATE connection sees it — not an in-object cache.
        got = await reader.read_last_receive_at("telegram")
        assert got == clock.now()
    finally:
        await writer_pool.close()
        await reader_pool.close()


# 6. Heartbeat gating on updater.running ------------------------------------
class _RecordingStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def mark_alive(self, channel: str) -> None:
        self.calls.append(channel)


class _FakeUpdater:
    def __init__(self, running: bool) -> None:
        self.running = running


class _FakeApp:
    def __init__(self, running: bool) -> None:
        self.updater = _FakeUpdater(running)


async def test_beat_only_stamps_when_updater_running() -> None:
    store = _RecordingStore()
    adapter = TelegramChannelAdapter(TelegramSettings(), liveness=store)  # type: ignore[arg-type]

    adapter._bot_app = _FakeApp(running=False)
    await adapter._beat_once()
    assert store.calls == []  # dead updater -> no stamp -> goes stale -> degraded

    adapter._bot_app = _FakeApp(running=True)
    await adapter._beat_once()
    assert store.calls == ["telegram"]
