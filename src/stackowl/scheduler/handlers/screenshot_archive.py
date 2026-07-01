"""ScreenshotArchiveHandler — daily/weekly screenshots of a URL list."""

from __future__ import annotations

import contextlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.browser._logging import url_path_only

if TYPE_CHECKING:
    from stackowl.tools.browser.runtime import CamoufoxRuntime


class ScreenshotArchiveHandler(JobHandler):
    """Takes screenshots of every URL in ``params['urls']`` and writes to a dated folder.

    Required job ``params``: ``{"urls": ["..."]}``.
    Optional: ``{"full_page": false}``.
    """

    def __init__(self, runtime: CamoufoxRuntime, archive_root: Path) -> None:
        self._runtime = runtime
        self._archive_root = archive_root
        self._archive_root.mkdir(parents=True, exist_ok=True)

    @property
    def handler_name(self) -> str:
        return "screenshot_archive"

    @property
    def trigger_kind(self) -> TriggerKind:
        # ON_DEMAND, not seeded: execute() REQUIRES params['urls']; a standing
        # blank-param row would fail every poll. Jobs are enqueued per
        # user-configured URL list, so no boot-time row is expected (WS-G).
        return "on_demand"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        urls_raw = job.params.get("urls", [])
        urls = [str(u) for u in urls_raw] if isinstance(urls_raw, list) else []
        full_page = bool(job.params.get("full_page", False))
        log.scheduler.info(
            "[scheduler] screenshot_archive.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "url_count": len(urls), "full_page": full_page}},
        )
        if not urls:
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error="Missing 'urls' list in params", duration_ms=0.0,
            )
        if not self._runtime.available:
            return JobResult(
                job_id=job.job_id,
                effect_class="state_change", success=False, output=None,
                error=f"Browser unavailable: {self._runtime.unavailable_reason or 'not started'}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        TestModeGuard.assert_not_test_mode("screenshot_archive.execute")

        date_dir = self._archive_root / datetime.now(UTC).strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        captured: list[str] = []
        errors: list[str] = []

        for url in urls:
            ctx: Any = None
            page: Any = None
            try:
                await self._runtime.acquire_domain_slot(url)
                ctx = await self._runtime.open_context(owner_key="scheduler")
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await self._runtime.record_navigation()
                ts = int(time.time() * 1000)
                out_path = date_dir / f"{ts}-{abs(hash(url)) % 10**8}.png"
                await page.screenshot(path=str(out_path), full_page=full_page)
                with contextlib.suppress(OSError):
                    out_path.chmod(0o600)
                captured.append(str(out_path))
            except Exception as exc:
                errors.append(f"{url_path_only(url)}: {exc}")
                log.scheduler.warning(
                    "[scheduler] screenshot_archive: capture failed",
                    exc_info=exc,
                    extra={"_fields": {"url": url_path_only(url)}},
                )
            finally:
                if page is not None:
                    with contextlib.suppress(Exception):
                        await page.close()
                if ctx is not None:
                    with contextlib.suppress(Exception):
                        await ctx.close()

        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] screenshot_archive.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "captured": len(captured),
                "errors": len(errors),
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=len(errors) == 0,
            output=f"captured={len(captured)} errors={len(errors)} dir={date_dir}",
            error="; ".join(errors) if errors else None,
            duration_ms=duration_ms,
            metadata={"captured_count": len(captured), "error_count": len(errors)},
        )


def register_screenshot_archive_handler(runtime: CamoufoxRuntime, archive_root: Path) -> None:
    handler = ScreenshotArchiveHandler(runtime=runtime, archive_root=archive_root)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] screenshot_archive handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
