"""PB-CANARY — generalized ChannelLivenessContributor's ``kind="send"`` path.

The default (``kind="receive"``) regression pin lives in
``test_pb0b_channel_liveness.py`` alongside PB0b's original witnesses. These
tests cover the NEW send-kind behavior: fresh timestamp -> healthy, stale ->
down, with send-specific wording distinct from the receive wording.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from stackowl.channels.liveness import ChannelLivenessStore
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.health.contributors import ChannelLivenessContributor


class FakeClock:
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


async def test_send_kind_contributor_name(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        contrib = ChannelLivenessContributor(
            store, "telegram_canary", clock, kind="send", stale_after_s=2400.0
        )
        assert contrib.contributor_name == "telegram_canary_send"
    finally:
        await pool.close()


async def test_send_kind_fresh_is_ok(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram_canary")
        clock.advance(60)
        contrib = ChannelLivenessContributor(
            store, "telegram_canary", clock, kind="send", stale_after_s=2400.0
        )
        status = await contrib.health_check()
        assert status.status == "ok"
        # Send wording must differ from the receive wording (not just status).
        assert "receive" not in (status.message or "")
        assert "send" in (status.message or "")
    finally:
        await pool.close()


async def test_send_kind_stale_is_down_with_custom_threshold(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram_canary")
        clock.advance(2401)  # just past the 2400s stale_after_s
        contrib = ChannelLivenessContributor(
            store, "telegram_canary", clock, kind="send", stale_after_s=2400.0
        )
        status = await contrib.health_check()
        assert status.status == "degraded"
        assert "receive" not in (status.message or "")
        assert "send" in (status.message or "")
    finally:
        await pool.close()


async def test_send_kind_never_alive_is_down(tmp_path: Path) -> None:
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        contrib = ChannelLivenessContributor(
            store, "telegram_canary", clock, kind="send", stale_after_s=2400.0
        )
        status = await contrib.health_check()
        assert status.status == "down"
        assert "receive" not in (status.message or "")
    finally:
        await pool.close()


async def test_send_kind_default_stale_after_s_uses_module_default(
    tmp_path: Path,
) -> None:
    """Omitting ``stale_after_s`` for a send-kind contributor falls back to the
    same module constant PB0b's receive contributor uses (120s) — a distinct
    canary registration always passes its own 2400s explicitly, but the
    parameter's OWN default must still be honest/consistent."""
    clock = FakeClock()
    pool = await _migrated_pool(tmp_path)
    try:
        store = ChannelLivenessStore(pool, clock)
        await store.mark_alive("telegram_canary")
        clock.advance(121)  # just past the 120s module default
        contrib = ChannelLivenessContributor(store, "telegram_canary", clock, kind="send")
        status = await contrib.health_check()
        assert status.status == "degraded"
    finally:
        await pool.close()
