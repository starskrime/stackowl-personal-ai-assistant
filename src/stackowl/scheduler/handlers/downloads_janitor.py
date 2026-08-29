"""DownloadsJanitorHandler — prune old files from the workspace downloads folder.

The single canonical downloads folder (``~/.stackowl/workspace/downloads/``) is a
scratch area: everything the agent downloads or fetches for the user lands there
so ``send_file`` can deliver it. Left unmanaged it grows without bound, so this
built-in scheduler handler runs every 12h and deletes files older than 2 days
from THAT folder only — never the workspace root or its durable stores.

Mirrors :mod:`browser_cache_eviction` exactly (same self-healing eviction shape,
4-point logging, ``register_*`` factory). The eviction helper is a local copy of
the proven template so the two handlers stay independent (no private cross-handler
import).
"""

from __future__ import annotations

import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


def _evict_older_than(directory: Path, max_age_days: int) -> tuple[int, int]:
    """Remove files older than ``max_age_days``. Returns ``(removed_count, bytes_freed)``.

    Self-healing: a missing directory yields ``(0, 0)``; an ``OSError`` on stat or
    unlink is caught + logged and that entry is skipped — the sweep never raises.
    Only regular files are removed; directories (incl. empty ones) are left alone.
    """
    if not directory.exists():
        return 0, 0
    cutoff = time.time() - (max_age_days * 86_400)
    removed = 0
    freed = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        try:
            size = stat.st_size
            path.unlink()
            removed += 1
            freed += size
        except OSError as exc:
            log.scheduler.warning(
                "[scheduler] downloads_janitor: unlink failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
    return removed, freed


class DownloadsJanitorHandler(JobHandler):
    """Periodic prune of the workspace downloads folder.

    Optional job ``params``: ``{"max_age_days": 2}`` (default ``2``).
    """

    def __init__(
        self, downloads_dir: Path, extra_dirs: tuple[Path, ...] = ()
    ) -> None:
        self._downloads_dir = downloads_dir
        # D03.4 level 2 — the tool-result spill directory decays here rather than
        # in a janitor of its own. _evict_older_than is already generic over a
        # directory, this job already runs every 12h and already self-heals, so a
        # second sweeper would be a second engine for one rule.
        #
        # It matters: 22% of tool results exceed 10k chars and the largest spilled
        # would be 4,201,658, on a box whose root filesystem is already 87% full.
        # An append-only spill directory is the "no decay" shape pool.py records
        # the cost of — a 922 MB database, 70% free pages, surfacing as
        # `database is locked` and a task loop failing every tick.
        self._extra_dirs = tuple(extra_dirs)

    @property
    def handler_name(self) -> str:
        return "downloads_janitor"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        max_age_days = int(job.params.get("max_age_days", 2))
        log.scheduler.info(
            "[scheduler] downloads_janitor.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id,
                "downloads_dir": str(self._downloads_dir),
                "max_age_days": max_age_days,
            }},
        )
        removed, freed = _evict_older_than(self._downloads_dir, max_age_days)
        for extra in self._extra_dirs:
            r, f = _evict_older_than(extra, max_age_days)
            removed += r
            freed += f
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] downloads_janitor.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "files_removed": removed,
                "bytes_freed": freed,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=f"removed={removed} freed_bytes={freed}",
            error=None,
            duration_ms=duration_ms,
            metadata={
                "files_removed": removed,
                "freed_bytes": freed,
                "max_age_days": max_age_days,
            },
        )


def register_downloads_janitor_handler(downloads_dir: Path | None = None) -> None:
    """Construct + register the janitor on the process registry.

    ``downloads_dir`` defaults to :meth:`StackowlHome.downloads_dir` (the single
    canonical workspace downloads folder).
    """
    from stackowl.paths import StackowlHome

    target = downloads_dir or StackowlHome.downloads_dir()
    # D03.4 level 2 — sweep the tool-result spill directory too. Passing it HERE
    # is what makes the reaper real: an extra_dirs parameter nobody supplies is a
    # decay leg that never runs, which is the same "built but not wired" shape
    # that left the Supervisor unused by the channel loops.
    spill = StackowlHome.home() / "sandbox" / "tool_results"
    handler = DownloadsJanitorHandler(downloads_dir=target, extra_dirs=(spill,))
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] downloads_janitor handler registered",
        extra={"_fields": {"handler": handler.handler_name,
                           "downloads_dir": str(target),
                           "extra_dirs": [str(spill)]}},
    )
