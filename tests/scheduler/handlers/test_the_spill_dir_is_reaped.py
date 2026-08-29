"""Spilled tool results must decay, or they poison the disk they were saved to.

D03.4 level 2 writes a full oversized result under
~/.stackowl/sandbox/tool_results/<trace>/. The measured worst case is 4,201,658
characters from a single browser_extract call, and 22% of all results already
exceed 10k — an append-only spill directory would grow without bound on a Jetson
whose root filesystem is already 87% full.

"Anything that only appends will poison its reader" is the fourth recurring defect
shape in this tree, and pool.py records what it cost last time: a 922 MB database
of which 70% was free pages, on a filesystem at 99%, surfacing as `database is
locked` and a task loop failing every tick.

REUSED, NOT REBUILT. downloads_janitor already prunes by age with a generic
_evict_older_than(directory, max_age_days), already runs every 12h, and already
self-heals (a missing directory yields (0,0); an OSError is logged and skipped).
Adding a second janitor for a second directory would be a second engine for one
rule.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stackowl.scheduler.handlers.downloads_janitor import DownloadsJanitorHandler
from stackowl.scheduler.job import Job


def _aged_file(d: Path, name: str, days: float) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("x" * 100, encoding="utf-8")
    old = time.time() - days * 86_400
    import os
    os.utime(p, (old, old))
    return p


def _job() -> Job:
    return Job(
        job_id="janitor-test", handler_name="downloads_janitor",
        schedule="every 12h", idempotency_key="janitor-test",
        last_run_at=None, next_run_at="2026-08-29T00:00:00", status="pending",
        params={"max_age_days": 2},
    )


@pytest.mark.asyncio
async def test_an_old_spilled_result_is_removed(tmp_path: Path) -> None:
    """THE regression. Without this the spill directory only ever grows."""
    downloads, spill = tmp_path / "downloads", tmp_path / "tool_results"
    old = _aged_file(spill / "trace-a", "browser_extract-1.txt", days=5)

    await DownloadsJanitorHandler(downloads, extra_dirs=(spill,)).execute(_job())

    assert not old.exists(), "the spilled result was never reaped"


@pytest.mark.asyncio
async def test_a_RECENT_spill_survives(tmp_path: Path) -> None:
    """The control, and the one that matters for correctness.

    A spill exists so the agent can re-read it on THIS turn. Reaping it eagerly
    would destroy the very thing level 2 was built to keep.
    """
    downloads, spill = tmp_path / "downloads", tmp_path / "tool_results"
    fresh = _aged_file(spill / "trace-b", "browser_extract-2.txt", days=0)

    await DownloadsJanitorHandler(downloads, extra_dirs=(spill,)).execute(_job())

    assert fresh.exists(), "a spill from this turn was reaped while still needed"


@pytest.mark.asyncio
async def test_downloads_are_still_swept(tmp_path: Path) -> None:
    """The existing behaviour must be byte-identical — this extends, not replaces."""
    downloads, spill = tmp_path / "downloads", tmp_path / "tool_results"
    old_dl = _aged_file(downloads, "report.pdf", days=5)

    await DownloadsJanitorHandler(downloads, extra_dirs=(spill,)).execute(_job())

    assert not old_dl.exists()


@pytest.mark.asyncio
async def test_no_extra_dirs_behaves_exactly_as_before(tmp_path: Path) -> None:
    """Every existing construction site passes one directory and must be unchanged."""
    downloads = tmp_path / "downloads"
    old_dl = _aged_file(downloads, "old.bin", days=5)

    result = await DownloadsJanitorHandler(downloads).execute(_job())

    assert not old_dl.exists()
    assert result.success is True


def test_the_registration_actually_passes_the_spill_dir() -> None:
    """The wiring property. An extra_dirs nobody supplies never reaps anything.

    Same shape as the Supervisor that existed, worked, and was not applied to the
    channel loops — capability built, not wired.
    """
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src" / "stackowl" / "scheduler" / "handlers" / "downloads_janitor.py"
    ).read_text(encoding="utf-8")

    assert "extra_dirs=(spill,)" in src, (
        "the janitor is registered without the spill directory, so spilled tool "
        "results are never reaped"
    )
