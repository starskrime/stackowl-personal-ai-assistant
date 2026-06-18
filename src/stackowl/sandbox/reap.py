"""SandboxReaper — the bounded, never-raising reap primitives for E11-S6 GC.

Reaps the three kinds of LEAKED sandbox artifacts a crash/kill can leave behind.
Each source is STATE/AGE-guarded so a LIVE concurrent run is NEVER reaped — a sweep
firing during an in-flight run must not kill it. (A live run lives at most
``DEFAULT_WALL_TIME_S`` ≈ 30s; the TTL is :data:`~stackowl.sandbox.limits.SANDBOX_ARTIFACT_TTL_S`,
~120× that.) The guard per source:

* **Scratch dirs** under ``~/.stackowl/sandbox/<tag>`` — removed only when their mtime
  age EXCEEDS the TTL; a live run's <30s dir is spared. The reserved ``seccomp`` subdir
  (the shared, durable seccomp profile) is NEVER reaped.
* **Docker containers** named ``stackowl-sbx-*`` — ``docker rm -f`` only those that are
  ``exited`` OR older than the TTL; a RUNNING container younger than the TTL (a live run)
  is SPARED. Guards for no docker on PATH (no-op + log).
* **bwrap cgroup scopes** — transient ``--user`` ``stackowl-sbx-*.scope`` units; stopped
  only when their ActiveState is ``inactive``/``failed``; an ``active`` scope (a live run)
  is SPARED. Guards for a non-systemd host (no-op + log).

Fail-safe everywhere: when a state/age cannot be parsed, the artifact is SPARED (never
reap what cannot be proven a leak).

Every method is bounded (a hung daemon/systemctl must not wedge the sweep) and
NEVER raises (B5) — a failure returns a count of what it managed and logs the rest.
Clock-injected (ARCH-99) for deterministic TTL tests. No vendor names in identifiers
(``docker`` / ``systemctl`` are the real binaries, used neutrally).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.sandbox.limits import SANDBOX_ARTIFACT_TTL_S

__all__ = ["SandboxReaper"]

# The per-run artifact name prefix (container ``--name`` / bwrap ``.scope`` unit).
_ARTIFACT_PREFIX = "stackowl-sbx-"
# Reserved subdir under ~/.stackowl/sandbox/ that is NOT a per-run scratch and must
# never be reaped (the shared, durable seccomp profile lives there).
_RESERVED_SCRATCH = frozenset({"seccomp"})
# Bounded timeout for the short control-plane commands (docker/systemctl).
_CONTROL_TIMEOUT_S = 15.0


class SandboxReaper:
    """Bounded, never-raising reap of leaked sandbox scratch / containers / scopes."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        scratch_root: Path | None = None,
        ttl_s: float = SANDBOX_ARTIFACT_TTL_S,
        docker_bin: str = "docker",
        systemctl_bin: str = "systemctl",
    ) -> None:
        self._clock = clock or WallClock()
        self._scratch_root = scratch_root or (StackowlHome.home() / "sandbox")
        self._ttl_s = ttl_s
        self._docker = docker_bin
        self._systemctl = systemctl_bin

    # ------------------------------------------------------------- scratch dirs
    def reap_scratch(self) -> int:
        """Remove per-run scratch dirs older than the TTL. Returns the count. Never raises.

        Age is by mtime against the injected clock's wall time; the reserved
        ``seccomp`` subdir is skipped. A live run's dir (mtime within the last ~30s)
        is far younger than the TTL and is therefore never touched.
        """
        log.tool.debug(
            "[sandbox.reap] reap_scratch: entry",
            extra={"_fields": {"root": str(self._scratch_root), "ttl_s": self._ttl_s}},
        )
        root = self._scratch_root
        if not root.is_dir():
            return 0
        now = self._clock.now().timestamp()
        reaped = 0
        try:
            children = list(root.iterdir())
        except OSError as exc:
            log.tool.warning(
                "[sandbox.reap] reap_scratch: cannot list root — skipping",
                extra={"_fields": {"err": type(exc).__name__}},
            )
            return 0
        for child in children:
            if child.name in _RESERVED_SCRATCH:
                continue
            try:
                age = now - child.stat().st_mtime
            except OSError:
                continue  # vanished mid-sweep — fine
            if age <= self._ttl_s:
                continue  # too fresh — could be a live run; NEVER reap
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
                reaped += 1
            except OSError as exc:
                log.tool.warning(
                    "[sandbox.reap] reap_scratch: remove failed — continuing",
                    extra={"_fields": {"name": child.name, "err": type(exc).__name__}},
                )
        log.tool.debug("[sandbox.reap] reap_scratch: exit", extra={"_fields": {"reaped": reaped}})
        return reaped

    # ------------------------------------------------------------- containers
    async def reap_containers(self) -> int:
        """Force-remove LEAKED ``stackowl-sbx-*`` docker containers. Never raises.

        State/age-guarded so a LIVE concurrent run is NEVER reaped: a container is
        reaped ONLY if it is ``exited`` OR older than the TTL. A RUNNING container
        younger than the TTL is an in-flight run and is SPARED. No docker on PATH →
        no-op + debug log (cross-platform / no-docker host). Fail-safe: if the created
        timestamp cannot be parsed for a running container, it is SPARED (never reap
        what we cannot prove is a leak). A single failed ``rm`` is logged; the rest
        still proceed.
        """
        if shutil.which(self._docker) is None:
            log.tool.debug("[sandbox.reap] reap_containers: docker absent — no-op")
            return 0
        ok, out = await self._cmd(
            [self._docker, "ps", "-a", "--filter", f"name={_ARTIFACT_PREFIX}",
             "--format", "{{.Names}}\t{{.State}}\t{{.CreatedAt}}"]
        )
        if not ok:
            return 0
        names = self._stale_containers(out)
        reaped = 0
        for name in names:
            rm_ok, _ = await self._cmd([self._docker, "rm", "-f", name])
            if rm_ok:
                reaped += 1
            else:
                log.tool.warning(
                    "[sandbox.reap] reap_containers: rm failed — continuing",
                    extra={"_fields": {"name": name}},
                )
        log.tool.debug(
            "[sandbox.reap] reap_containers: exit",
            extra={"_fields": {"found": len(names), "reaped": reaped}},
        )
        return reaped

    def _stale_containers(self, listing: str) -> list[str]:
        """Names of ``stackowl-sbx-*`` containers that are leaks (exited OR past TTL).

        Each line is ``name\\tstate\\tcreated``. A container is stale (reapable) iff its
        state is ``exited`` OR its age exceeds the TTL. A RUNNING container younger than
        the TTL is a live run and is SPARED. Fail-safe: an unparseable created timestamp
        on a non-exited container means we cannot prove a leak → SPARE it.
        """
        now = self._clock.now().timestamp()
        stale: list[str] = []
        for line in listing.splitlines():
            parts = line.split("\t")
            name = parts[0].strip() if parts else ""
            if not name.startswith(_ARTIFACT_PREFIX):
                continue
            state = parts[1].strip().lower() if len(parts) > 1 else ""
            if state == "exited":
                stale.append(name)
                continue
            created = parts[2].strip() if len(parts) > 2 else ""
            ts = self._parse_docker_created(created)
            if ts is None:
                continue  # fail-safe — cannot prove a leak; SPARE a possibly-live run
            if (now - ts) > self._ttl_s:
                stale.append(name)
        return stale

    @staticmethod
    def _parse_docker_created(created: str) -> float | None:
        """Parse docker's ``CreatedAt`` to an epoch, or ``None`` if unparseable.

        Docker's default ``CreatedAt`` looks like ``2026-06-02 11:22:33 +0000 UTC``
        (a trailing ``UTC``/zone label follows the numeric offset). We keep the date,
        time and numeric offset and drop the trailing label. Returns ``None`` on any
        parse failure so the caller can fail-safe (spare the artifact).
        """
        if not created:
            return None
        from datetime import datetime

        parts = created.split()
        # Expect at least: <date> <time> <offset>; drop a trailing zone label (e.g. UTC).
        if len(parts) < 3:
            return None
        stamp = f"{parts[0]} {parts[1]} {parts[2]}"
        for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S.%f %z"):
            try:
                return datetime.strptime(stamp, fmt).timestamp()
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------- cgroup scopes
    async def reap_scopes(self) -> int:
        """Stop LEAKED ``stackowl-sbx-*.scope`` systemd ``--user`` units. Never raises.

        State-guarded so a LIVE concurrent run is NEVER reaped: a scope is reaped ONLY
        if its ActiveState is ``inactive`` or ``failed`` (a finished/leaked unit). A
        live run's scope is ``active`` and is SPARED. Non-systemd host (no ``systemctl``
        on PATH) → no-op + debug log. Fail-safe: an unrecognised/missing ActiveState
        column means we cannot prove a leak → SPARE the unit. A single failed stop is
        logged; the rest still proceed.
        """
        if shutil.which(self._systemctl) is None:
            log.tool.debug("[sandbox.reap] reap_scopes: systemctl absent — no-op")
            return 0
        ok, out = await self._cmd(
            [self._systemctl, "--user", "list-units", "--all", "--no-legend",
             "--plain", "--type=scope", f"{_ARTIFACT_PREFIX}*.scope"]
        )
        if not ok:
            return 0
        units = self._parse_units(out)
        reaped = 0
        for unit in units:
            stop_ok, _ = await self._cmd([self._systemctl, "--user", "stop", unit])
            if stop_ok:
                reaped += 1
            else:
                log.tool.warning(
                    "[sandbox.reap] reap_scopes: stop failed — continuing",
                    extra={"_fields": {"unit": unit}},
                )
        log.tool.debug(
            "[sandbox.reap] reap_scopes: exit",
            extra={"_fields": {"found": len(units), "reaped": reaped}},
        )
        return reaped

    @staticmethod
    def _parse_units(listing: str) -> list[str]:
        """Names of LEAKED ``stackowl-sbx-*.scope`` units (ActiveState inactive/failed).

        ``list-units --plain --no-legend`` rows are ``UNIT LOAD ACTIVE SUB ...``; the
        third whitespace-column is the ActiveState. A unit is reapable iff that state
        is ``inactive`` or ``failed``; an ``active`` unit is a live run and is SPARED.
        Fail-safe: a missing/unparseable ActiveState column → SPARE (never reap what we
        cannot prove is a leak).
        """
        reapable_states = frozenset({"inactive", "failed"})
        units: list[str] = []
        for line in listing.splitlines():
            cols = line.strip().split()
            if not cols:
                continue
            name = cols[0]
            if not (name.startswith(_ARTIFACT_PREFIX) and name.endswith(".scope")):
                continue
            active_state = cols[2].lower() if len(cols) > 2 else ""
            if active_state in reapable_states:
                units.append(name)
        return units

    # ------------------------------------------------------------- primitive
    async def _cmd(self, argv: list[str]) -> tuple[bool, str]:
        """Run a short control command, bounded. Returns ``(ok, stdout)``. Never raises."""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603 — fixed argv
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _err = await asyncio.wait_for(
                proc.communicate(), timeout=_CONTROL_TIMEOUT_S
            )
        except (OSError, TimeoutError, ValueError) as exc:
            log.tool.debug(
                "[sandbox.reap] _cmd: command failed",
                extra={"_fields": {"argv": argv[:2], "err": type(exc).__name__}},
            )
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            return False, ""
        if proc.returncode != 0:
            return False, ""
        return True, out.decode("utf-8", errors="replace")
