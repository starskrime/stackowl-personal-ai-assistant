"""A database beside the live one is detected on every collect and removed only by the heal.

MEASURED on the operator's box 2026-09-12: `~/.stackowl/stackowl.db` is 0 bytes. It was
deleted on 2026-09-08 and recreated twice on 2026-09-11. SQLite creates a missing file
instead of failing, so every ad-hoc `sqlite3.connect()` at the obvious guess — the
database beside the config — creates it again. Nothing in the platform noticed.

DETECTION AND REMOVAL ARE SEPARATE. `StrayDatabaseContributor` runs on every
`collect()` (the sweep, anything else reading health) and removes nothing; it reports
`degraded`, never `down`. `StrayDatabaseHealer` is the sweep's heal step: it removes
only a file that is exactly 0 bytes, has no `-wal`/`-shm`/`-journal` sibling, is not the
live database and has not been modified for a whole sweep interval — a connection
opened a moment ago looks exactly like a decoy. Every removal is recorded, so a stray
that keeps returning says so.

THE SCOPE IS TWO DIRECTORIES, NEITHER DESCENDED: the home root and the live database's
own directory. The same box holds legitimate non-empty databases NESTED under the home
(browser-profile NSS stores, pre-migration backups, a pre-restore snapshot).
"""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.health.contributors import (
    _SQLITE_SIDECARS,
    STRAY_DATABASE_REMOVED_EVENT,
    DbContributor,
    StrayDatabaseContributor,
    StrayDatabaseHealer,
)
from stackowl.scheduler.handlers.health_sweep import HEALTH_SWEEP_INTERVAL_MINUTES

pytestmark = pytest.mark.asyncio


def _live_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        # WAL, like the live database: a read-only open may add -wal/-shm beside it,
        # which is the one thing "untouched" does not promise.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE jobs (id INTEGER)")
        conn.execute("INSERT INTO jobs VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    return path


def _aged(path: Path) -> Path:
    """Last modified longer ago than one sweep — the heal leaves anything younger alone."""
    if not path.exists():
        path.touch()
    then = time.time() - HEALTH_SWEEP_INTERVAL_MINUTES * 60 - 60
    os.utime(path, (then, then))
    return path


def _modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


def _databases(directory: Path) -> list[str]:
    """Database files only — SQLite may add -wal/-shm beside a WAL database."""
    return sorted(p.name for p in directory.iterdir() if p.suffix == ".db")


class _Removals:
    """Answers the durable removal count the way `DbPool.fetch_all` would."""

    def __init__(self, n: int) -> None:
        self.n = n

    async def fetch_all(self, sql: str, params: tuple[object, ...]) -> list[dict[str, int]]:
        return [{"n": self.n}]


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def append(self, **row: object) -> None:
        self.rows.append(row)


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    return root


@pytest.fixture()
def live(home: Path) -> Path:
    return _live_db(home / "workspace" / "stackowl.db")


class TestCollectingDetectsAndRemovesNothing:
    async def test_an_aged_empty_stray_is_degraded_with_a_remedy_and_left_in_place(
        self, home: Path, live: Path
    ) -> None:
        stray = _aged(home / "stackowl.db")

        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "degraded", "a stray is not a broken critical subsystem"
        assert str(stray) in (status.message or "")
        assert status.remedy
        assert stray.exists(), "collecting health deleted a file"

    async def test_a_stray_younger_than_one_sweep_is_only_noted(
        self, home: Path, live: Path
    ) -> None:
        stray = home / "stackowl.db"
        stray.touch()

        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "ok"
        assert str(stray) in (status.message or ""), "a young stray must still be named"
        assert stray.exists()

    async def test_a_database_with_data_is_degraded_never_down_and_never_touched(
        self, home: Path, live: Path
    ) -> None:
        other = _aged(_live_db(home / "other.db"))
        size = other.stat().st_size
        before = other.read_bytes()

        status = await StrayDatabaseContributor(home, live).health_check()
        await StrayDatabaseHealer(home, live).ensure_available()

        assert status.status == "degraded"
        assert str(other) in (status.message or "")
        assert str(size) in (status.message or ""), "the report must carry the size"
        assert status.remedy
        assert other.read_bytes() == before, "a stray holding data was modified"

    @pytest.mark.parametrize("sidecar", _SQLITE_SIDECARS)
    async def test_an_empty_file_with_a_sidecar_is_data_not_a_decoy(
        self, home: Path, live: Path, sidecar: str
    ) -> None:
        """Committed rows can sit in the WAL while the main file is still 0 bytes."""
        stray = _aged(home / "stackowl.db")
        companion = home / f"stackowl.db{sidecar}"
        companion.write_bytes(b"not empty")

        status = await StrayDatabaseContributor(home, live).health_check()
        await StrayDatabaseHealer(home, live).ensure_available()

        assert status.status == "degraded"
        assert str(stray) in (status.message or "")
        assert stray.exists(), f"removed a database whose {sidecar} sibling may hold its data"
        assert companion.read_bytes() == b"not empty"

    async def test_a_stray_beside_the_live_database_is_seen(
        self, home: Path, live: Path
    ) -> None:
        stray = _aged(live.parent / "other.db")

        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "degraded"
        assert str(stray) in (status.message or "")

    async def test_backups_beside_the_live_database_are_not_strays_but_data_at_the_root_is(
        self, home: Path, live: Path
    ) -> None:
        """MEASURED after the 2026-09-12 restart: the live box carries
        `workspace/stackowl-backup-pre-negative-purge-20260625.db` (14,942,208 bytes), and
        reporting it paged on every sweep. Data beside the live database is an operator's
        backup; data at the home root, where no writer puts a database, is a stray."""
        backups = [
            _aged(_live_db(live.parent / "stackowl-backup-pre-negative-purge-20260625.db")),
            _aged(_live_db(live.parent / "pre-migration-20260913T011735091589.db")),
        ]
        before = [b.read_bytes() for b in backups]

        clean = await StrayDatabaseContributor(home, live).health_check()
        await StrayDatabaseHealer(home, live).ensure_available()
        other = _aged(_live_db(home / "other.db"))
        with_root_stray = await StrayDatabaseContributor(home, live).health_check()

        assert clean.status == "ok", clean.message
        assert [b.read_bytes() for b in backups] == before, "a backup was touched"
        assert with_root_stray.status == "degraded"
        assert str(other) in (with_root_stray.message or "")
        assert not any(str(b) in (with_root_stray.message or "") for b in backups)

    async def test_a_stray_that_came_back_names_its_recurrence(
        self, home: Path, live: Path
    ) -> None:
        _aged(home / "stackowl.db")

        status = await StrayDatabaseContributor(home, live, db=_Removals(2)).health_check()

        assert status.status == "degraded"
        assert "removed 2 time(s) before" in (status.message or "")
        assert "keeps coming back" in (status.remedy or "")


class TestTheHealRemovesOnlyAgedEmptyStrays:
    async def test_an_aged_empty_stray_is_removed_logged_and_recorded(
        self, home: Path, live: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stray = _aged(home / "stackowl.db")
        modified = _modified(stray)
        audit = _Audit()
        before = live.read_bytes()

        with caplog.at_level(logging.INFO, logger="stackowl.health"):
            await StrayDatabaseHealer(home, live, db=_Removals(0), audit=audit).ensure_available()
        after = await StrayDatabaseContributor(home, live).health_check()

        assert not stray.exists(), "the aged 0-byte decoy survived the heal"
        assert any(
            str(stray) in r.getMessage() and modified in r.getMessage() for r in caplog.records
        ), "the heal must log the removed file and when it was last modified"
        assert audit.rows == [{
            "event_type": STRAY_DATABASE_REMOVED_EVENT, "actor": "health_sweep",
            "target": str(stray), "details": {"last_modified": modified},
        }], "a removal that is not recorded cannot be counted when the stray comes back"
        assert after.status == "ok"
        assert live.read_bytes() == before, "the live database was touched"

    async def test_a_young_empty_stray_is_left_for_the_next_sweep(
        self, home: Path, live: Path
    ) -> None:
        stray = home / "stackowl.db"
        stray.touch()

        await StrayDatabaseHealer(home, live).ensure_available()

        assert stray.exists(), "removed a file that may be a connection opening right now"

    async def test_a_stray_that_came_back_is_removed_at_warning(
        self, home: Path, live: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        stray = _aged(home / "stackowl.db")

        with caplog.at_level(logging.INFO, logger="stackowl.health"):
            await StrayDatabaseHealer(home, live, db=_Removals(1)).ensure_available()

        assert not stray.exists()
        assert any(
            r.levelno >= logging.WARNING and str(stray) in r.getMessage() for r in caplog.records
        ), "a stray that keeps coming back was removed quietly again"

    async def test_a_removal_that_fails_raises_so_the_sweep_escalates(
        self, home: Path, live: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stray = _aged(home / "stackowl.db")

        def _refuse(self: Path, *args: object, **kwargs: object) -> None:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))

        monkeypatch.setattr(Path, "unlink", _refuse)
        with pytest.raises(OSError, match="could not remove"):
            await StrayDatabaseHealer(home, live).ensure_available()

        assert stray.exists()

    async def test_a_file_someone_else_already_removed_is_healed_not_failed(
        self, home: Path, live: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _aged(home / "stackowl.db")

        def _gone(self: Path, *args: object, **kwargs: object) -> None:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(self))

        monkeypatch.setattr(Path, "unlink", _gone)
        await StrayDatabaseHealer(home, live).ensure_available()  # must not raise


class TestTheRealDatabaseIsNeverAStray:
    async def test_a_healthy_home_is_ok(self, home: Path, live: Path) -> None:
        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "ok"

    async def test_an_empty_live_database_at_the_home_root_is_left_alone_and_is_down(
        self, home: Path
    ) -> None:
        """STACKOWL_DATA_DIR pointed at the home puts `db_path()` at the home root. It is
        never a stray — but an EMPTY live database is the data-loss state, so the db
        contributor reports it down."""
        real = _aged(home / "stackowl.db")

        stray_status = await StrayDatabaseContributor(home, real).health_check()
        await StrayDatabaseHealer(home, real).ensure_available()
        db_status = await DbContributor(real).health_check()

        assert stray_status.status == "ok"
        assert real.exists(), "the live database was removed for being empty"
        assert db_status.status == "down"
        assert db_status.remedy

    async def test_the_live_database_is_matched_as_the_same_file_not_the_same_string(
        self, home: Path, live: Path
    ) -> None:
        """A second name for the live file (as a differently-cased home is on a
        case-insensitive filesystem) must not make it a stray."""
        alias = home / "alias.db"
        try:
            os.link(live, alias)
        except (OSError, NotImplementedError):
            pytest.skip("this filesystem cannot hard-link")

        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "ok", status.message

    async def test_a_symlink_is_never_followed_out_of_the_home(
        self, home: Path, live: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside" / "empty.db"
        outside.parent.mkdir()
        _aged(outside)
        link = home / "stackowl.db"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("this platform cannot create a symlink here")

        await StrayDatabaseHealer(home, live).ensure_available()

        assert outside.exists(), "a file outside the home was deleted through a symlink"
        assert link.is_symlink()

    async def test_databases_nested_under_the_home_are_out_of_scope(
        self, home: Path, live: Path
    ) -> None:
        """Measured on the live box: these exist, are legitimate, and hold data."""
        nss = _live_db(home / "browser-profiles" / "local" / "p" / "key4.db")
        backup = _live_db(
            home / "workspace" / "knowledge" / "backups" / "pre-migration-x" / "stackowl.db"
        )

        status = await StrayDatabaseContributor(home, live).health_check()

        assert status.status == "ok", status.message
        assert nss.exists() and backup.exists()


class TestTheDbPingCannotCreateTheFileItChecks:
    async def test_a_present_database_is_ok_and_its_file_untouched(self, live: Path) -> None:
        before = (live.read_bytes(), live.stat().st_mtime_ns)

        status = await DbContributor(live).health_check()

        assert status.status == "ok"
        assert (live.read_bytes(), live.stat().st_mtime_ns) == before
        assert _databases(live.parent) == ["stackowl.db"], "the ping created another database"

    async def test_an_absent_database_is_down_and_is_not_created(self, tmp_path: Path) -> None:
        path = tmp_path / "workspace" / "stackowl.db"
        path.parent.mkdir()

        status = await DbContributor(path).health_check()

        assert status.status == "down"
        assert not path.exists(), "the health check created the database it was asking about"

    async def test_an_empty_live_database_is_down_with_a_remedy(self, tmp_path: Path) -> None:
        path = tmp_path / "stackowl.db"
        path.touch()

        status = await DbContributor(path).health_check()

        assert status.status == "down", "an empty live database answered SELECT 1 and read as ok"
        assert status.remedy

    async def test_the_ping_opens_read_only(
        self, live: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The existence check and the connect are two moments; only `mode=ro` closes the gap."""
        seen: list[tuple[tuple[object, ...], dict[str, object]]] = []
        real_connect = sqlite3.connect

        def _spy(*args: object, **kwargs: object) -> sqlite3.Connection:
            seen.append((args, kwargs))
            return real_connect(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite3, "connect", _spy)
        status = await DbContributor(live).health_check()

        assert status.status == "ok"
        assert seen, "the ping never connected"
        assert all("mode=ro" in str(args[0]) and kwargs.get("uri") for args, kwargs in seen)
