"""BrowserCacheEvictionHandler — prune old browser cache + screenshot files."""

from __future__ import annotations

import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult


def _evict_older_than(directory: Path, max_age_days: int) -> tuple[int, int]:
    """Remove files older than ``max_age_days``. Returns ``(removed_count, bytes_freed)``."""
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
                "[scheduler] browser_cache_eviction: unlink failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
    return removed, freed


class BrowserCacheEvictionHandler(JobHandler):
    """Daily prune of ``~/.stackowl/cache/browser/`` and ``~/.stackowl/screenshots/``.

    Optional job ``params``: ``{"cache_max_age_days": 7, "screenshots_max_age_days": 30}``.
    """

    def __init__(self, cache_dir: Path, screenshots_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._screenshots_dir = screenshots_dir

    @property
    def handler_name(self) -> str:
        return "browser_cache_eviction"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        cache_age = int(job.params.get("cache_max_age_days", 7))
        screen_age = int(job.params.get("screenshots_max_age_days", 30))
        log.scheduler.info(
            "[scheduler] browser_cache_eviction.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id,
                "cache_age_days": cache_age,
                "screen_age_days": screen_age,
            }},
        )
        cache_removed, cache_freed = _evict_older_than(self._cache_dir, cache_age)
        screen_removed, screen_freed = _evict_older_than(self._screenshots_dir, screen_age)
        total_removed = cache_removed + screen_removed
        total_freed = cache_freed + screen_freed
        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] browser_cache_eviction.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "files_removed": total_removed,
                "bytes_freed": total_freed,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            success=True,
            output=f"removed={total_removed} freed_bytes={total_freed}",
            error=None,
            duration_ms=duration_ms,
            metadata={
                "cache_removed": cache_removed,
                "cache_freed_bytes": cache_freed,
                "screenshots_removed": screen_removed,
                "screenshots_freed_bytes": screen_freed,
            },
        )


def register_browser_cache_eviction_handler(cache_dir: Path, screenshots_dir: Path) -> None:
    handler = BrowserCacheEvictionHandler(cache_dir=cache_dir, screenshots_dir=screenshots_dir)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] browser_cache_eviction handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
