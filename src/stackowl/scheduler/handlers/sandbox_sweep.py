"""SandboxSweepHandler — reap LEAKED sandbox artifacts on a schedule (E11-S6).

A clean sandbox run cleans up after itself (the backend removes its scratch + the
container in a ``finally`` block). But a CRASH / SIGKILL / power loss can leak:

* **scratch dirs** under ``~/.stackowl/sandbox/<tag>`` (the run's workspace), and
* **docker containers** named ``stackowl-sbx-*`` (run without ``--rm`` so a reaper
  can inspect them), and
* **bwrap cgroup scopes** — transient systemd ``--user`` ``stackowl-sbx-*.scope``
  units that a dead launcher never tore down.

Without a recurring reaper these accumulate forever. This handler drives a bounded,
NEVER-raising sweep on a schedule (seeded ``every 10m`` in the scheduler assembly,
mirroring :class:`ProcessSweepHandler`). Each reap source is independent: a failure
in one is logged and the others still run (self-healing, B5). Clock-injected
(ARCH-99) so the TTL is deterministically testable.

Live-run safety (why a LIVE run is NEVER reaped, for ALL three sources): each reap is
STATE/AGE-guarded. Scratch dirs are removed only when their mtime age EXCEEDS the TTL
(:data:`SANDBOX_ARTIFACT_TTL_S`, 3600s — ~120× the ``DEFAULT_WALL_TIME_S`` 30s a backend
hard-kills at). Docker containers are removed only when ``exited`` OR past the TTL (a
RUNNING container younger than the TTL — a live run — is SPARED). Cgroup scopes are
stopped only when ``inactive``/``failed`` (an ``active`` scope — a live run — is SPARED).
A 10-min sweep firing during an in-flight run therefore cannot kill it. Fail-safe: an
unparseable state/age means the artifact is SPARED.

Cross-platform: the docker / systemd reaps GUARD for the absence of their tool
(no docker on PATH, non-systemd host) → that source is a no-op + a debug log, never
a crash. On such hosts only the scratch-dir reap (always available) runs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.sandbox.reap import SandboxReaper
from stackowl.scheduler.base import HandlerRegistry, JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from pathlib import Path

__all__ = ["SandboxSweepHandler", "register_sandbox_sweep_handler"]


class SandboxSweepHandler(JobHandler):
    """Recurring reaper of leaked sandbox scratch dirs, containers, and scopes.

    Owns a :class:`SandboxReaper` (the bounded, never-raising reap primitives) and
    drives all three sources once per :meth:`execute`, reporting the reap counts.
    Each source heals independently so a misbehaving reaper can never crash the
    scheduler loop (self-healing, B5).
    """

    def __init__(
        self, *, clock: Clock | None = None, scratch_root: Path | None = None
    ) -> None:
        self._reaper = SandboxReaper(clock=clock or WallClock(), scratch_root=scratch_root)

    @property
    def handler_name(self) -> str:
        return "sandbox_sweep"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.info(
            "[scheduler] sandbox_sweep.execute: entry",
            extra={"_fields": {"job_id": job.job_id}},
        )
        counts: dict[str, int] = {"scratch": 0, "containers": 0, "scopes": 0}
        # 3. STEP — each reap source is independent + never-raises; a failure in one
        # is logged inside the reaper and the others still run (self-healing).
        try:
            counts["scratch"] = self._reaper.reap_scratch()
        except Exception as exc:  # belt — the reaper already heals internally
            log.scheduler.error(
                "[scheduler] sandbox_sweep: scratch reap failed — continuing",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
        try:
            counts["containers"] = await self._reaper.reap_containers()
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] sandbox_sweep: container reap failed — continuing",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
        try:
            counts["scopes"] = await self._reaper.reap_scopes()
        except Exception as exc:
            log.scheduler.error(
                "[scheduler] sandbox_sweep: scope reap failed — continuing",
                exc_info=exc, extra={"_fields": {"job_id": job.job_id}},
            )
        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] sandbox_sweep.execute: exit",
            extra={"_fields": {"job_id": job.job_id, **counts, "duration_ms": duration_ms}},
        )
        return JobResult(
            job_id=job.job_id,
            effect_class="state_change",
            success=True,
            output=(
                f"scratch={counts['scratch']} containers={counts['containers']} "
                f"scopes={counts['scopes']}"
            ),
            error=None,
            duration_ms=duration_ms,
            metadata=dict(counts),
        )


def register_sandbox_sweep_handler(
    *, clock: Clock | None = None, scratch_root: Path | None = None
) -> SandboxSweepHandler:
    """Construct and register the :class:`SandboxSweepHandler` singleton.

    Mirrors :func:`register_process_sweep_handler`: registers the handler on the
    :class:`HandlerRegistry` so the scheduler can dispatch a ``sandbox_sweep`` job to
    it. The recurring JOB row itself is seeded separately in the scheduler assembly
    (``every 10m``, alongside the other minute-scale recurring handlers).
    """
    handler = SandboxSweepHandler(clock=clock, scratch_root=scratch_root)
    HandlerRegistry.instance().register(handler)
    log.scheduler.info(
        "[scheduler] sandbox_sweep handler registered",
        extra={"_fields": {"handler": handler.handler_name}},
    )
    return handler
