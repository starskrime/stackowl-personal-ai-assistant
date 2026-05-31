"""Tests for :class:`DownloadsJanitorHandler` — the 12h downloads cleanup janitor.

Covers: ``handler_name`` is ``"downloads_janitor"``; ``_evict_older_than`` deletes
files older than the cutoff and keeps recent ones, returning ``(removed, freed)``;
a missing directory yields ``(0, 0)`` and never raises; an ``unlink`` failure is
logged not raised (self-healing); ``execute`` returns a successful ``JobResult``
with metadata; ``register_downloads_janitor_handler`` puts the handler on the
process registry.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.downloads_janitor import (
    DownloadsJanitorHandler,
    _evict_older_than,
    register_downloads_janitor_handler,
)
from stackowl.scheduler.job import Job, JobResult

_THREE_DAYS = 3 * 86_400


def _make_job(*, params: dict[str, Any] | None = None) -> Job:
    return Job(
        job_id=f"downloads_janitor-{uuid.uuid4().hex[:6]}",
        handler_name="downloads_janitor",
        schedule="every 12h",
        idempotency_key=uuid.uuid4().hex,
        last_run_at=None,
        next_run_at=datetime.now(UTC).isoformat(),
        status="pending",
        params=params or {},
    )


def _write_old(path: Path, *, days_old: float, content: bytes = b"x") -> None:
    path.write_bytes(content)
    old = time.time() - (days_old * 86_400)
    os.utime(path, (old, old))


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    HandlerRegistry.reset()
    yield
    HandlerRegistry.reset()


def test_handler_name_is_downloads_janitor() -> None:
    handler = DownloadsJanitorHandler(downloads_dir=Path("/nonexistent"))
    assert handler.handler_name == "downloads_janitor"


def test_evict_removes_old_keeps_recent(tmp_path: Path) -> None:
    old1 = tmp_path / "old1.bin"
    old2 = tmp_path / "old2.bin"
    fresh = tmp_path / "fresh.bin"
    _write_old(old1, days_old=3, content=b"aaaa")
    _write_old(old2, days_old=10, content=b"bb")
    _write_old(fresh, days_old=0.5, content=b"keep")

    removed, freed = _evict_older_than(tmp_path, max_age_days=2)

    assert removed == 2
    assert freed == 6  # 4 + 2 bytes
    assert not old1.exists()
    assert not old2.exists()
    assert fresh.exists()


def test_evict_missing_dir_returns_zero(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert _evict_older_than(missing, max_age_days=2) == (0, 0)


def test_evict_recurses_into_subdirs_but_leaves_dirs(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    old = sub / "old.bin"
    _write_old(old, days_old=5)

    removed, _ = _evict_older_than(tmp_path, max_age_days=2)

    assert removed == 1
    assert not old.exists()
    # The directory itself is left alone.
    assert sub.exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="chmod read-only dir does not block unlink on Windows",
)
def test_evict_unlink_failure_is_logged_not_raised(tmp_path: Path) -> None:
    # A file in a read-only directory cannot be unlinked → OSError branch.
    locked = tmp_path / "locked"
    locked.mkdir()
    victim = locked / "old.bin"
    _write_old(victim, days_old=5)
    os.chmod(locked, 0o500)  # r-x: cannot remove children
    try:
        removed, freed = _evict_older_than(tmp_path, max_age_days=2)
        # The unlink failed defensively: nothing reported removed, no raise.
        assert removed == 0
        assert freed == 0
        assert victim.exists()
    finally:
        os.chmod(locked, 0o700)  # restore so tmp cleanup works


@pytest.mark.asyncio
async def test_execute_returns_successful_jobresult(tmp_path: Path) -> None:
    old = tmp_path / "old.bin"
    fresh = tmp_path / "fresh.bin"
    _write_old(old, days_old=5, content=b"abc")
    _write_old(fresh, days_old=0.1)

    handler = DownloadsJanitorHandler(downloads_dir=tmp_path)
    result = await handler.execute(_make_job())

    assert isinstance(result, JobResult)
    assert result.success is True
    assert result.metadata["files_removed"] == 1
    assert result.metadata["freed_bytes"] == 3
    assert result.metadata["max_age_days"] == 2  # default
    assert result.output == "removed=1 freed_bytes=3"
    assert not old.exists()
    assert fresh.exists()


@pytest.mark.asyncio
async def test_execute_honors_param_max_age_days(tmp_path: Path) -> None:
    f = tmp_path / "f.bin"
    _write_old(f, days_old=3)

    handler = DownloadsJanitorHandler(downloads_dir=tmp_path)
    # With a 5-day retention, the 3-day-old file survives.
    result = await handler.execute(_make_job(params={"max_age_days": 5}))

    assert result.metadata["files_removed"] == 0
    assert result.metadata["max_age_days"] == 5
    assert f.exists()


def test_register_puts_handler_on_registry(tmp_path: Path) -> None:
    register_downloads_janitor_handler(downloads_dir=tmp_path)
    registered = HandlerRegistry.instance().get("downloads_janitor")
    assert isinstance(registered, DownloadsJanitorHandler)
    assert registered.handler_name == "downloads_janitor"


def test_register_defaults_to_stackowl_downloads_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("STACKOWL_DATA_DIR", raising=False)
    from stackowl.paths import StackowlHome

    register_downloads_janitor_handler()  # no explicit dir
    handler = HandlerRegistry.instance().get("downloads_janitor")
    assert isinstance(handler, DownloadsJanitorHandler)
    assert handler._downloads_dir == StackowlHome.downloads_dir()
