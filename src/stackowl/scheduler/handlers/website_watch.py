"""WebsiteWatchHandler — poll a URL, hash the content, ping when it changes.

Lets owls schedule jobs like "watch this product page; ping me when the price
drops" or "watch this changelog; notify me on a new release". Backed by the
Camoufox runtime so JS-rendered SPAs work the same as plain HTML.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.browser._extraction import extract_markdown
from stackowl.tools.browser._logging import url_path_only

if TYPE_CHECKING:
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer
    from stackowl.tools.browser.runtime import CamoufoxRuntime


class WebsiteWatchHandler(JobHandler):
    """Polls a URL and emits a job-result diff when the canonical content hash changes.

    Required job ``params``: ``{"url": "..."}``.
    Optional: ``{"selector": "css-selector", "mode": "text" | "dom_hash"}``.
    """

    def __init__(
        self,
        runtime: CamoufoxRuntime,
        state_dir: Path,
        job_deliverer: ProactiveJobDeliverer | None = None,
    ) -> None:
        self._runtime = runtime
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)
        # The shared cron-born delivery loop (single seam + exactly-once ledger).
        # Absent it, a detected change is recorded honestly but never sent (no fake
        # "delivered") — same wiring-gap honesty the other proactive handlers use.
        self._job_deliverer = job_deliverer

    @property
    def handler_name(self) -> str:
        return "website_watch"

    @property
    def trigger_kind(self) -> TriggerKind:
        # Created by the cronjob `watch` action on a user request — no standing
        # SchedulerAssembly seed. Declares on_demand so the wiring audit does not
        # flag it as dangling.
        return "on_demand"

    def _state_file(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self._state_dir / f"watch-{digest}.json"

    def _load_state(self, url: str) -> dict[str, Any]:
        path = self._state_file(url)
        if not path.exists():
            return {}
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.scheduler.warning(
                "[scheduler] website_watch._load_state: state read failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            return {}

    def _save_state(self, url: str, state: dict[str, Any]) -> None:
        path = self._state_file(url)
        try:
            path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            log.scheduler.warning(
                "[scheduler] website_watch._save_state: state write failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        url = str(job.params.get("url", ""))
        log.scheduler.info(
            "[scheduler] website_watch.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "url": url_path_only(url)}},
        )
        if not url:
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error="Missing 'url' in params", duration_ms=0.0,
            )
        if not self._runtime.available:
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error=f"Browser unavailable: {self._runtime.unavailable_reason or 'not started'}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        TestModeGuard.assert_not_test_mode("website_watch.execute")
        ctx: Any = None
        page: Any = None
        try:
            await self._runtime.acquire_domain_slot(url)
            ctx = await self._runtime.open_context(owner_key="scheduler")
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await self._runtime.record_navigation()
            html = await page.content()
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] website_watch.execute: fetch failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
            )
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            if ctx is not None:
                with contextlib.suppress(Exception):
                    await ctx.close()

        text = extract_markdown(html, include_links=False)
        current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        prev = self._load_state(url)
        prev_hash = prev.get("content_hash")
        changed = prev_hash is not None and prev_hash != current_hash
        new_state = {"content_hash": current_hash, "last_seen_at": time.time(), "url": url}
        self._save_state(url, new_state)

        # On a REAL change (never the first poll) deliver a concise human ping
        # through the SAME durable, exactly-once seam goal_execution/check_in use —
        # addressed from the job's persisted target (no _last_* guess). The first
        # poll establishes a baseline (changed=False) so no spurious ping fires.
        delivery: str | None = None
        if changed and self._job_deliverer is not None:
            msg = f"🔔 The page you're watching changed: {url}"
            outcome = await self._job_deliverer.deliver_for_job(
                job, message=msg, category="website_watch", urgency="normal"
            )
            # HONESTY — record the real rollup; never claim a change-notify was
            # sent when it was undeliverable/failed/suppressed.
            delivery = outcome.rollup
            log.scheduler.info(
                "[scheduler] website_watch.execute: change delivered",
                extra={"_fields": {"job_id": job.job_id, "delivery": delivery}},
            )
        elif changed and self._job_deliverer is None:
            # Detected a change but nothing is wired to send it — surface the gap
            # honestly rather than pretending it went out.
            delivery = "no_deliverer"
            log.scheduler.warning(
                "[scheduler] website_watch.execute: change detected but no "
                "deliverer wired — not sent (honest)",
                extra={"_fields": {"job_id": job.job_id}},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] website_watch.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id,
                "url": url_path_only(url),
                "changed": changed,
                "first_seen": prev_hash is None,
                "delivery": delivery,
                "duration_ms": duration_ms,
            }},
        )
        metadata: dict[str, Any] = {
            "changed": changed,
            "first_seen": prev_hash is None,
            "content_hash": current_hash,
        }
        if delivery is not None:
            metadata["delivery"] = delivery
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery",
            success=True,
            output=f"changed={changed} hash={current_hash[:12]}",
            error=None,
            duration_ms=duration_ms,
            metadata=metadata,
        )


def register_website_watch_handler(
    runtime: CamoufoxRuntime,
    state_dir: Path,
    job_deliverer: ProactiveJobDeliverer | None = None,
) -> None:
    handler = WebsiteWatchHandler(
        runtime=runtime, state_dir=state_dir, job_deliverer=job_deliverer
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] website_watch handler registered",
        extra={
            "_fields": {
                "handler": handler.handler_name,
                "delivery_wired": job_deliverer is not None,
            }
        },
    )
