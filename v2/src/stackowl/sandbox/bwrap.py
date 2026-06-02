"""BwrapSandbox — the PRIMARY rootless, no-network code-execution backend (E11-S3).

This is the keystone trust boundary in practice: it runs UNTRUSTED / LLM-generated
python in a rootless bubblewrap (``bwrap``) user-namespace sandbox with NO network
and a cgroup-v2 resource cage. It upholds the seven :class:`SandboxBackend`
invariants:

#1 never un-isolated — every run goes through bwrap inside a capped cgroup; any
   setup failure REFUSES (a structured ``ExecResult``), never a bare host process.
#2 caps-or-refuse — the run is launched AS the payload of a delegated cgroup-v2
   scope (:class:`~stackowl.sandbox.cgroup.CgroupRecipe`) that enforces memory.max +
   pids.max; if a mandatory cap cannot be written the recipe exits non-zero and the
   backend REFUSES. CPU is capped by cgroup when delegated, else by the mandatory
   wall-time kill.
#3 deny-all network — ``--unshare-net`` gives the child an isolated, empty network
   namespace; ``supports_network=False`` so a ``network=True`` spec is REFUSED.
#4 env allowlist-from-empty — ``--clearenv`` then ``--setenv`` only for the names in
   ``spec.env_allow`` (host secrets never cross); HOME is pinned to /workspace.
#5 never raise — every failure path returns a structured :class:`ExecResult`.
#6 no host FS — only a minimal read-only OS runtime + the run's own RW scratch are
   mounted (:mod:`stackowl.sandbox.mounts`); /home, ~/.stackowl, /etc are absent.
#7 no priv-escalation — rootless user namespace (no setuid, no ``--uid 0``),
   ``--unshare-all`` drops every other namespace, ``--cap-drop ALL`` when supported.

The scratch workdir lives under ``~/.stackowl/sandbox/<session>`` (all-state-in-home)
and is the ONLY writable mount; it is removed in a ``finally`` (the cgroup scope is
auto-reaped by systemd when the payload exits).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from pathlib import Path

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.paths import StackowlHome
from stackowl.process.kill_platform import terminate_tree
from stackowl.sandbox.argv import BwrapArgvBuilder
from stackowl.sandbox.base import SandboxAvailability, SandboxBackend
from stackowl.sandbox.capability import SandboxCapability
from stackowl.sandbox.cgroup import CGROUP_MARKER, CgroupRecipe
from stackowl.sandbox.spec import ExecResult, ExecSpec

__all__ = ["BwrapSandbox"]


class BwrapSandbox(SandboxBackend):
    """Rootless, no-network bubblewrap backend. The selector's primary choice."""

    def __init__(self, *, clock: Clock | None = None, enabled: bool = True) -> None:
        self._clock = clock if clock is not None else WallClock()
        self._argv = BwrapArgvBuilder()
        # config gate (settings.sandbox.bwrap_enabled) — a disabled backend reports
        # unavailable so the selector never picks it.
        self._enabled = enabled

    # ------------------------------------------------------------- identity
    @property
    def name(self) -> str:
        return "bwrap"

    @property
    def is_rootless(self) -> bool:
        return True

    @property
    def supports_network(self) -> bool:
        # bwrap cannot grant safe network egress; network runs route to Docker.
        return False

    # ------------------------------------------------------------- availability
    async def is_available(self) -> SandboxAvailability:
        """bwrap present AND cgroup-v2 caps enforceable. Never raises (B5)."""
        log.tool.debug("[sandbox.bwrap] is_available: entry")
        if not self._enabled:
            return SandboxAvailability.no(
                "bwrap sandbox backend disabled by config (sandbox.bwrap_enabled=false)"
            )
        try:
            probe = SandboxCapability.probe()
            if not probe.bwrap_viable:
                return SandboxAvailability.no(probe.bwrap_reason)
            cg_ok, cg_reason = CgroupRecipe.delegation_available()
            if not cg_ok:
                # caps-or-refuse (#2): present but cannot enforce caps → unavailable.
                return SandboxAvailability.no(
                    f"bwrap present but cgroup-v2 resource caps unavailable "
                    f"({cg_reason}) — cannot enforce mandatory caps"
                )
            return SandboxAvailability.ok()
        except Exception as exc:  # B5
            log.tool.error("[sandbox.bwrap] is_available: probe failed", exc_info=exc)
            return SandboxAvailability.no(f"bwrap availability check failed ({type(exc).__name__})")

    # ------------------------------------------------------------- run
    async def run(self, spec: ExecSpec) -> ExecResult:
        """Execute ``spec`` in an isolated bwrap+cgroup cage. Never raises (B5)."""
        started = self._clock.monotonic()
        # 1. ENTRY — log shape only, NEVER the code content (length only).
        log.tool.debug(
            "[sandbox.bwrap] run: entry",
            extra={
                "_fields": {
                    "code_len": len(spec.code),
                    "network": spec.network,
                    "timeout_s": spec.timeout_s,
                    "session": spec.session_id or "-",
                }
            },
        )
        # invariant #3: this backend never grants network — refuse rather than run unsafely.
        if spec.network:
            return ExecResult.error(
                reason="denied",
                message="bwrap cannot grant network access; route network runs to a network-capable backend",
                backend_used=self.name,
                caps_applied=spec.caps,
            )

        scratch: Path | None = None
        try:
            scratch = self._make_scratch(spec.session_id)
            self._write_code(scratch, spec.code)
            return await self._launch(spec, scratch, started)
        except Exception as exc:  # B5 — any unexpected failure is structured, never raised.
            log.tool.error("[sandbox.bwrap] run: unexpected failure", exc_info=exc)
            return ExecResult.error(
                reason="sandbox_error",
                message=f"sandbox setup failed ({type(exc).__name__}: {exc})",
                backend_used=self.name,
                caps_applied=spec.caps,
                duration_ms=self._elapsed_ms(started),
            )
        finally:
            self._cleanup_scratch(scratch)

    # ------------------------------------------------------------- launch
    async def _launch(self, spec: ExecSpec, scratch: Path, started: float) -> ExecResult:
        """Build bwrap+cgroup argv, run it, and map the outcome to an ExecResult."""
        marker = scratch / CGROUP_MARKER
        # Only the ``workspace`` subdir is bound RW — the cgroup marker file lives
        # in the scratch ROOT and is NOT exposed to the child (invariant #6).
        bwrap_argv = self._argv.build(spec, scratch / "workspace")
        unit = f"stackowl-sbx-{self._session_tag(spec.session_id)}"
        argv = CgroupRecipe.build_command(
            caps=spec.caps, unit=unit, marker_path=marker, bwrap_argv=bwrap_argv
        )
        # The argv is NOT secret — log it at debug for isolation auditability (the
        # code lives in the scratch file, not the argv; no env VALUES are logged).
        log.tool.debug(
            "[sandbox.bwrap] _launch: argv built",
            extra={"_fields": {"bwrap_argv": bwrap_argv, "unit": unit}},
        )
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603 — argv from our builders
                *argv,
                stdin=asyncio.subprocess.PIPE if spec.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            log.tool.error("[sandbox.bwrap] _launch: spawn failed", exc_info=exc)
            return ExecResult.error(
                reason="sandbox_error",
                message=f"failed to launch the sandbox ({type(exc).__name__}: {exc})",
                backend_used=self.name,
                caps_applied=spec.caps,
                duration_ms=self._elapsed_ms(started),
            )

        stdin_bytes = spec.stdin.encode("utf-8") if spec.stdin is not None else None
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin_bytes), timeout=spec.timeout_s)
        except TimeoutError:
            timed_out = True
            log.tool.info(
                "[sandbox.bwrap] _launch: wall-time exceeded — killing sandbox",
                extra={"_fields": {"timeout_s": spec.timeout_s, "pid": proc.pid}},
            )
            await terminate_tree(proc.pid)
            out, err = await self._drain(proc)

        return self._map_result(spec, proc, marker, out, err, timed_out, started)

    # ------------------------------------------------------------- result mapping
    def _map_result(
        self,
        spec: ExecSpec,
        proc: asyncio.subprocess.Process,
        marker: Path,
        out: bytes,
        err: bytes,
        timed_out: bool,
        started: float,
    ) -> ExecResult:
        """Translate the process outcome into a provenance-tagged ExecResult."""
        duration = self._elapsed_ms(started)
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        code = proc.returncode
        if timed_out:
            return ExecResult.timed_out(
                stdout=stdout, stderr=stderr, backend_used=self.name,
                network_enabled=False, caps_applied=spec.caps, duration_ms=duration,
            )
        # caps-or-refuse (#2): a recipe REFUSE exit means a mandatory cap could not
        # be enforced — surface it as a sandbox_error, never a (mis-reported) ok.
        if CgroupRecipe.is_refusal_exit(code):
            log.tool.error(
                "[sandbox.bwrap] _map_result: cgroup recipe refused (cap unenforceable)",
                extra={"_fields": {"exit_code": code}},
            )
            return ExecResult.error(
                reason="sandbox_error", message=CgroupRecipe.refusal_message(code),
                backend_used=self.name, caps_applied=spec.caps, duration_ms=duration,
            )
        # OOM detection: the kernel SIGKILLs a process that breaches memory.max; the
        # cgroup's oom_kill counter confirms it was an OOM (vs a user kill / clean exit).
        if CgroupRecipe.oom_killed(marker):
            log.tool.info("[sandbox.bwrap] _map_result: cgroup OOM-killed the run", extra={"_fields": {"code": code}})
            return ExecResult.error(
                reason="oom", message="the run exceeded its memory cap and was OOM-killed",
                backend_used=self.name, caps_applied=spec.caps, duration_ms=duration,
            )
        # A negative returncode = killed by a SIGNAL (a late OOM whose oom_kill counter
        # read raced the scope teardown, an external SIGKILL, a segfault) — that is NOT a
        # clean exit and must NEVER be mis-classified as "ok".
        if code is not None and code < 0:
            log.tool.info(
                "[sandbox.bwrap] _map_result: run killed by signal",
                extra={"_fields": {"signal": -code}},
            )
            return ExecResult.error(
                reason="killed",
                message=f"the run was killed by signal {-code} (memory cap or external kill)",
                backend_used=self.name, caps_applied=spec.caps, duration_ms=duration,
            )
        log.tool.debug(
            "[sandbox.bwrap] run: exit",
            extra={"_fields": {"exit_code": code, "duration_ms": duration, "stdout_len": len(stdout)}},
        )
        return ExecResult.ok(
            stdout=stdout, stderr=stderr, exit_code=code if code is not None else -1,
            backend_used=self.name, network_enabled=False, caps_applied=spec.caps, duration_ms=duration,
        )

    # ------------------------------------------------------------- scratch + util
    def _make_scratch(self, session_id: str) -> Path:
        """Create the 0700 scratch + ``workspace`` subdir under ~/.stackowl/sandbox.

        Returns the scratch ROOT (which holds ``workspace/`` and the cgroup marker);
        the code goes in ``workspace/main.py`` and only ``workspace`` is bind-mounted.
        """
        root = StackowlHome.home() / "sandbox"
        root.mkdir(parents=True, exist_ok=True)
        scratch = root / (self._session_tag(session_id) or uuid.uuid4().hex)
        (scratch / "workspace").mkdir(parents=True, exist_ok=True)
        for d in (scratch, scratch / "workspace"):
            with contextlib.suppress(OSError):
                d.chmod(0o700)
        return scratch

    @staticmethod
    def _write_code(scratch: Path, code: str) -> None:
        """Write the run's python entrypoint into the scratch workspace."""
        (scratch / "workspace" / "main.py").write_text(code, encoding="utf-8")

    @staticmethod
    def _session_tag(session_id: str) -> str:
        """A filesystem-safe, collision-resistant tag for the scratch + cgroup unit."""
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:32]
        return f"{safe}-{uuid.uuid4().hex[:8]}" if safe else uuid.uuid4().hex[:16]

    @staticmethod
    async def _drain(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        """Collect whatever output a killed process left behind. Never raises."""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=2.0)
        except (TimeoutError, OSError, ValueError):
            return b"", b""

    def _cleanup_scratch(self, scratch: Path | None) -> None:
        """Remove the run's scratch tree. Never raises."""
        if scratch is None:
            return
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except OSError as exc:
            log.tool.debug("[sandbox.bwrap] _cleanup_scratch: rmtree failed", extra={"_fields": {"err": str(exc)}})

    def _elapsed_ms(self, started: float) -> int:
        return int((self._clock.monotonic() - started) * 1000)
