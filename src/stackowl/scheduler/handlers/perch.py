"""PerchHandler — watch a filesystem path, ping when its contents change.

The filesystem analog of :class:`WebsiteWatchHandler` and the "perch" concept
from the original design (CLAUDE.md) that had no Python implementation. It
snapshots a watched directory (or file), diffs against the prior poll, and on a
real change delivers a concise ping through the SAME durable exactly-once seam
the other proactive handlers use — addressed from the job's persisted target,
never a ``_last_*`` guess. The first poll establishes a baseline so no spurious
ping fires.

This is the autonomous "notices things on its own" sensor the platform lacked:
website-watching already existed; filesystem-watching did not. Created on demand
via the ``cronjob`` ``watch`` action (``watch_path``) — no standing seed.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log
from stackowl.scheduler.base import HandlerRegistry, JobHandler, TriggerKind
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.notifications.proactive_job import ProactiveJobDeliverer

#: Files larger than this are signed by (size, mtime) rather than a content hash
#: — a content hash of a huge file every poll would be wasteful. Smaller files
#: (the common case for watched notes/config dirs) are content-hashed so a change
#: is detected regardless of mtime granularity.
_MAX_HASH_BYTES = 5_000_000


class PerchHandler(JobHandler):
    """Polls a path and pings when its file set / contents change.

    Required job ``params``: ``{"path": "/dir/or/file"}``.
    Optional: ``{"glob": "*.md"}`` to restrict which files are watched.
    """

    def __init__(
        self,
        state_dir: Path,
        job_deliverer: ProactiveJobDeliverer | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)
        # The shared cron-born delivery loop (single seam + exactly-once ledger).
        # Absent it, a detected change is recorded honestly but never sent.
        self._job_deliverer = job_deliverer

    @property
    def handler_name(self) -> str:
        return "perch"

    @property
    def trigger_kind(self) -> TriggerKind:
        # Created by the cronjob `watch` action (watch_path) on a user request —
        # no standing SchedulerAssembly seed. on_demand so the wiring audit does
        # not flag it as dangling.
        return "on_demand"

    # --------------------------------------------------------------- state io

    def _state_file(self, path: str) -> Path:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
        return self._state_dir / f"perch-{digest}.json"

    def _load_state(self, path: str) -> dict[str, Any]:
        state_path = self._state_file(path)
        if not state_path.exists():
            return {}
        try:
            return dict(json.loads(state_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.scheduler.warning(
                "[scheduler] perch._load_state: state read failed",
                exc_info=exc,
                extra={"_fields": {"path": str(state_path)}},
            )
            return {}

    def _save_state(self, path: str, state: dict[str, Any]) -> None:
        state_path = self._state_file(path)
        try:
            state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            log.scheduler.warning(
                "[scheduler] perch._save_state: state write failed",
                exc_info=exc,
                extra={"_fields": {"path": str(state_path)}},
            )

    # ----------------------------------------------------------- scan + diff

    @staticmethod
    def _signature(file_path: Path) -> str:
        """A change-sensitive signature for one file: ``size:hash`` (or size:mtime)."""
        try:
            stat = file_path.stat()
        except OSError:
            return "missing"
        if stat.st_size <= _MAX_HASH_BYTES:
            try:
                digest = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]
                return f"{stat.st_size}:{digest}"
            except OSError:
                pass
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    @classmethod
    def _scan(cls, root: Path, glob: str) -> dict[str, str]:
        """Map ``relpath -> signature`` for every watched file under ``root``."""
        snapshot: dict[str, str] = {}
        if root.is_file():
            snapshot[root.name] = cls._signature(root)
            return snapshot
        if not root.is_dir():
            return snapshot
        for child in sorted(root.rglob(glob)):
            if child.is_file():
                snapshot[str(child.relative_to(root))] = cls._signature(child)
        return snapshot

    @staticmethod
    def _diff(prev: dict[str, str], cur: dict[str, str]) -> tuple[int, int, int]:
        """Return ``(added, modified, removed)`` counts between two snapshots."""
        prev_keys, cur_keys = set(prev), set(cur)
        added = len(cur_keys - prev_keys)
        removed = len(prev_keys - cur_keys)
        modified = sum(1 for k in (cur_keys & prev_keys) if prev[k] != cur[k])
        return added, modified, removed

    # --------------------------------------------------------------- execute

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        path = str(job.params.get("path", ""))
        glob = str(job.params.get("glob", "") or "*")
        log.scheduler.info(
            "[scheduler] perch.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "path": path, "glob": glob}},
        )
        if not path:
            return JobResult(
                job_id=job.job_id,
                effect_class="delivery", success=False, output=None,
                error="Missing 'path' in params", duration_ms=(time.monotonic() - t0) * 1000,
            )
        TestModeGuard.assert_not_test_mode("perch.execute")

        root = Path(path)
        current = self._scan(root, glob)
        prev_state = self._load_state(path)
        prev_snapshot = prev_state.get("snapshot")
        first_seen = prev_snapshot is None

        added = modified = removed = 0
        if not first_seen:
            added, modified, removed = self._diff(dict(prev_snapshot or {}), current)
        changed = (not first_seen) and bool(added or modified or removed)

        self._save_state(path, {"snapshot": current, "last_seen_at": time.time(), "path": path})

        delivery: str | None = None
        if changed and self._job_deliverer is not None:
            msg = (
                f"🔔 Changes in {path}: "
                f"+{added} new, ~{modified} modified, -{removed} removed."
            )
            outcome = await self._job_deliverer.deliver_for_job(
                job, message=msg, category="perch", urgency="normal"
            )
            delivery = outcome.rollup
            log.scheduler.info(
                "[scheduler] perch.execute: change delivered",
                extra={"_fields": {"job_id": job.job_id, "delivery": delivery}},
            )
        elif changed and self._job_deliverer is None:
            delivery = "no_deliverer"
            log.scheduler.warning(
                "[scheduler] perch.execute: change detected but no deliverer wired "
                "— not sent (honest)",
                extra={"_fields": {"job_id": job.job_id}},
            )

        duration_ms = (time.monotonic() - t0) * 1000
        log.scheduler.info(
            "[scheduler] perch.execute: exit",
            extra={"_fields": {
                "job_id": job.job_id, "changed": changed, "first_seen": first_seen,
                "added": added, "modified": modified, "removed": removed,
                "delivery": delivery, "duration_ms": duration_ms,
            }},
        )
        metadata: dict[str, Any] = {
            "changed": changed, "first_seen": first_seen,
            "added": added, "modified": modified, "removed": removed,
            "file_count": len(current),
        }
        if delivery is not None:
            metadata["delivery"] = delivery
        return JobResult(
            job_id=job.job_id,
            effect_class="delivery", success=True,
            output=f"changed={changed} files={len(current)}",
            error=None, duration_ms=duration_ms, metadata=metadata,
        )


def register_perch_handler(
    state_dir: Path,
    job_deliverer: ProactiveJobDeliverer | None = None,
) -> PerchHandler:
    """Construct + register a :class:`PerchHandler` on the singleton registry."""
    handler = PerchHandler(state_dir=state_dir, job_deliverer=job_deliverer)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] perch handler registered",
        extra={"_fields": {
            "handler": handler.handler_name,
            "delivery_wired": job_deliverer is not None,
        }},
    )
    return handler
