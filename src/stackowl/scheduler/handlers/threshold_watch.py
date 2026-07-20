"""ThresholdWatchHandler — poll a generic NUMERIC source, fire on a predicate cross.

The conditional sibling of :class:`WebsiteWatchHandler` (UniOwl ADR-C). Where
website_watch hashes content and pings on ANY change, this fetches a NUMBER from a
generic source, evaluates a predicate ``(op, threshold)``, and fires ONLY on a
false→true EDGE — with HYSTERESIS so a predicate that stays true on every poll does
not flood the user. It re-arms after the predicate crosses back to false.

The source is deliberately GENERIC and vendor-neutral: a URL whose rendered text
carries a number (fetched through the same Camoufox runtime website_watch uses).
NO market/crypto/finance SDK, no symbol literals — it is a numeric monitor, not a
ticker. See [[UNIOWL_ARCHITECTURE.md]] ADR-C.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import operator
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.net.ssrf_guard import guard_playwright_navigation
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult
from stackowl.tools.browser._extraction import extract_markdown
from stackowl.tools.browser._logging import url_path_only

if TYPE_CHECKING:
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer
    from stackowl.tools.browser.runtime import CamoufoxRuntime

# The five accepted comparison ops (mirrors ThresholdTrigger.op). Kept as a table
# so the predicate is data, not a branch ladder.
_OPS: dict[str, Callable[[float, float], bool]] = {
    "gt": operator.gt,
    "lt": operator.lt,
    "ge": operator.ge,
    "le": operator.le,
    "eq": operator.eq,
}

# First numeric token in a blob of text: optional sign, digits with optional
# thousands-separating commas, optional decimal part. Vendor- and language-neutral
# (no keyword list) — it matches the NUMBER, not the thing being measured.
# ponytail: assumes '.'-decimal / ','-grouping; add locale parsing if a comma-decimal
# source ever shows up.
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class ThresholdWatchHandler(JobHandler):
    """Polls a numeric source and emits a delivery ONLY on a false→true predicate edge.

    Required job ``params``: ``{"watch_source": "<url>", "op": "gt", "threshold": 70000}``.
    Optional: ``{"prompt": "...", "owner": "<owl>"}``. (``watch_source``, not
    ``source`` — ``source`` is reserved for the owl-lifecycle provenance marker.)
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
        # Same shared cron-born delivery seam website_watch uses (single seam +
        # exactly-once ledger). Absent it, a fire is recorded honestly but never
        # sent (no fake "delivered").
        self._job_deliverer = job_deliverer

    @property
    def handler_name(self) -> str:
        return "threshold_watch"

    @property
    def trigger_kind(self) -> TriggerKind:
        # Projected from a scheduled owl's ``threshold`` trigger by the lifecycle
        # reconcile loop (no standing SchedulerAssembly seed), exactly like
        # website_watch. Declares on_demand so the wiring audit does not flag it.
        return "on_demand"

    def _state_key(self, source: str, op: str, threshold: float) -> str:
        """Identity of a watch = its (source, op, threshold). Two owls watching the
        same URL with different thresholds keep independent edge state."""
        raw = f"{source}|{op}|{threshold}".encode()
        return hashlib.sha256(raw).hexdigest()[:24]

    def _state_file(self, key: str) -> Path:
        return self._state_dir / f"threshold-{key}.json"

    def _load_state(self, key: str) -> dict[str, Any]:
        path = self._state_file(key)
        if not path.exists():
            return {}
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.scheduler.warning(
                "[scheduler] threshold_watch._load_state: state read failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )
            return {}

    def _save_state(self, key: str, state: dict[str, Any]) -> None:
        path = self._state_file(key)
        try:
            path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            log.scheduler.warning(
                "[scheduler] threshold_watch._save_state: state write failed",
                exc_info=exc,
                extra={"_fields": {"path": str(path)}},
            )

    @staticmethod
    def _extract_number(text: str) -> float | None:
        """First numeric token in ``text`` → float, or None if none present."""
        match = _NUMBER_RE.search(text)
        if match is None:
            return None
        try:
            return float(match.group(0).replace(",", ""))
        except ValueError:
            return None

    async def _fetch_number(self, source: str) -> float | None:
        """Fetch ``source`` (a URL) and extract the first number from its text.

        ponytail: URL source only — that already covers any numeric web source
        generically. A named-tool/extractor ref is the documented upgrade path
        (ADR-C) and would branch here on a non-URL ``source``.
        """
        parsed = urlparse(source)
        if parsed.scheme not in ("http", "https"):
            log.scheduler.warning(
                "[scheduler] threshold_watch._fetch_number: unsupported source "
                "(only http(s) URLs supported)",
                extra={"_fields": {"scheme": parsed.scheme}},
            )
            return None
        ctx: Any = None
        page: Any = None
        try:
            await self._runtime.acquire_domain_slot(source)
            ctx = await self._runtime.open_context(owner_key="scheduler")
            # FX-05 follow-up — bypasses BrowserSessionRegistry.open(), so the
            # per-redirect-hop SSRF guard must be attached here directly.
            await ctx.route("**/*", guard_playwright_navigation)
            page = await ctx.new_page()
            await page.goto(source, wait_until="domcontentloaded", timeout=30_000)
            await self._runtime.record_navigation()
            html = await page.content()
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            if ctx is not None:
                with contextlib.suppress(Exception):
                    await ctx.close()
        text = extract_markdown(html, include_links=False)
        return self._extract_number(text)

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        source = str(job.params.get("watch_source", ""))
        op = str(job.params.get("op", ""))
        threshold_raw = job.params.get("threshold")
        log.scheduler.info(
            "[scheduler] threshold_watch.execute: entry",
            extra={"_fields": {
                "job_id": job.job_id, "source": url_path_only(source),
                "op": op, "threshold": threshold_raw,
            }},
        )
        # Validate params up front — a malformed projection fails honestly, never
        # silently no-ops.
        if not source or op not in _OPS or not isinstance(threshold_raw, (int, float)):
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error=f"Invalid threshold params (source/op/threshold): op={op!r}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )
        threshold = float(threshold_raw)
        if not self._runtime.available:
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error=f"Browser unavailable: {self._runtime.unavailable_reason or 'not started'}",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        TestModeGuard.assert_not_test_mode("threshold_watch.execute")
        try:
            value = await self._fetch_number(source)
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.error(
                "[scheduler] threshold_watch.execute: fetch failed",
                exc_info=exc,
                extra={"_fields": {"job_id": job.job_id, "duration_ms": duration_ms}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error=str(exc), duration_ms=duration_ms,
            )
        if value is None:
            duration_ms = (time.monotonic() - t0) * 1000
            log.scheduler.warning(
                "[scheduler] threshold_watch.execute: no numeric token in source",
                extra={"_fields": {"job_id": job.job_id, "source": url_path_only(source)}},
            )
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error="No numeric value found in source", duration_ms=duration_ms,
            )

        # 2. DECISION — evaluate the predicate, then EDGE-TRIGGER with HYSTERESIS.
        # Fire ONLY on a false→true transition (prev_state is False). The first
        # poll (prev_state None) ESTABLISHES a baseline and never fires — same
        # "first poll = baseline, no ping" contract website_watch uses. While the
        # predicate stays true (prev_state True) it does NOT re-fire (the flood
        # ADR-C warns about); it re-arms only after crossing back to false.
        # ponytail: re-arm is crossing-based only; add a time cooldown if a source
        # flaps across the threshold every poll.
        key = self._state_key(source, op, threshold)
        current_state = _OPS[op](value, threshold)
        prev = self._load_state(key)
        prev_state = prev.get("last_state")  # bool | None
        first_seen = prev_state is None
        fired = current_state and prev_state is False
        self._save_state(key, {
            "last_state": current_state,
            "last_value": value,
            "last_seen_at": time.time(),
            "source": source,
        })

        delivery: str | None = None
        if fired and self._job_deliverer is not None:
            owner = str(job.params.get("owner") or "owl")
            prompt = str(job.params.get("prompt") or "").strip()
            msg = prompt or (
                f"🔔 {owner} alert: {source} is now {value} ({op} {threshold})"
            )
            outcome = await self._job_deliverer.deliver_for_job(
                job, message=msg, category="threshold_watch", urgency="normal"
            )
            # HONESTY — record the real rollup; never claim sent when undeliverable.
            delivery = outcome.rollup
            log.scheduler.info(
                "[scheduler] threshold_watch.execute: fire delivered",
                extra={"_fields": {"job_id": job.job_id, "delivery": delivery}},
            )
        elif fired and self._job_deliverer is None:
            delivery = "no_deliverer"
            log.scheduler.warning(
                "[scheduler] threshold_watch.execute: predicate fired but no "
                "deliverer wired — not sent (honest)",
                extra={"_fields": {"job_id": job.job_id}},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] threshold_watch.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "source": url_path_only(source),
                "value": value, "state": current_state, "fired": fired,
                "first_seen": first_seen, "delivery": delivery,
                "duration_ms": duration_ms,
            }},
        )
        metadata: dict[str, Any] = {
            "fired": fired,
            "state": current_state,
            "value": value,
            "threshold": threshold,
            "op": op,
            "first_seen": first_seen,
        }
        if delivery is not None:
            metadata["delivery"] = delivery
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery",
            success=True,
            output=f"value={value} state={current_state} fired={fired}",
            error=None,
            duration_ms=duration_ms,
            metadata=metadata,
        )


def register_threshold_watch_handler(
    runtime: CamoufoxRuntime,
    state_dir: Path,
    job_deliverer: ProactiveJobDeliverer | None = None,
) -> None:
    handler = ThresholdWatchHandler(
        runtime=runtime, state_dir=state_dir, job_deliverer=job_deliverer
    )
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] threshold_watch handler registered",
        extra={
            "_fields": {
                "handler": handler.handler_name,
                "delivery_wired": job_deliverer is not None,
            }
        },
    )
