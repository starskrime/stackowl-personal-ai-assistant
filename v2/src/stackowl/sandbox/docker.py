"""DockerSandbox — the NETWORK-capable, hardened-container code-execution backend.

The Docker tier of the E11 keystone trust boundary: runs UNTRUSTED / LLM-generated
python inside a HARDENED container. The host's Docker daemon is ROOTFUL (runs as
root), so a container escape could reach host root — which makes the restrictive
**seccomp filter load-bearing and MANDATORY here** (unlike the rootless bwrap
backend, where a userns already contains an escape). The hardening flag set lives in
:class:`~stackowl.sandbox.docker_argv.DockerArgvBuilder` and the daemon-facing
primitives in :class:`~stackowl.sandbox.docker_control.DockerControl`; this class
provisions the image + seccomp profile, runs the container under a wall-time budget,
classifies the outcome, and ALWAYS reaps the container + scratch.

The seven :class:`SandboxBackend` invariants map to: #1 every run is a hardened
``docker run`` or a structured refusal (never a bare host process); #2 ``--memory``/
``--memory-swap`` (==memory, no swap)/``--cpus``/``--pids-limit`` all applied or
refuse; #3 ``--network=none`` by DEFAULT, ``bridge`` only on explicit opt-in; #4 no
host env inherited, only allowlisted names forwarded; #5 never raise — structured
``ExecResult`` always; #6 read-only rootfs, code mounted READ-ONLY, bounded writable
tmpfs, no host-sensitive mount; #7 ``--cap-drop=ALL`` + ``no-new-privileges`` + non-root
``--user`` + the MANDATORY restrictive seccomp profile (never ``unconfined``).

The pinned minimal python base image is auto-pulled on first use
([[feedback_agent_auto_install]]) under a :class:`TestModeGuard` gate (NO docker
pull/run in test mode). The ``~/.stackowl/sandbox/<session>`` scratch + the container
(``docker rm -f`` — no ``--rm``, which would race the OOM inspect) are both reaped in
a ``finally`` so nothing is ever left behind. Selector wiring (E11-S5): inject
``DockerSandbox()`` into ``SandboxSelector(backends=[BwrapSandbox(), DockerSandbox()])``;
this story does NOT wire the execute_code tool (that is S5).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from stackowl.infra.clock import Clock, WallClock
from stackowl.infra.observability import log
from stackowl.sandbox.base import SandboxAvailability, SandboxBackend
from stackowl.sandbox.capability import SandboxCapability
from stackowl.sandbox.docker_argv import DockerArgvBuilder
from stackowl.sandbox.docker_control import DockerControl
from stackowl.sandbox.docker_scratch import DockerScratch
from stackowl.sandbox.seccomp import SeccompProfile
from stackowl.sandbox.spec import ExecResult, ExecSpec

__all__ = ["BASE_IMAGE", "DockerSandbox"]

# Pinned minimal python base image (specific tag, never ``latest``; multi-arch).
BASE_IMAGE = "python:3.12-slim"

# Docker's exit code for a SIGKILL'd container (128 + 9); a hint for OOM/killed
# classification alongside the authoritative inspect probe.
_SIGKILL_EXIT = 137


class DockerSandbox(SandboxBackend):
    """Hardened, network-capable Docker backend. Seccomp is MANDATORY here."""

    def __init__(
        self, *, clock: Clock | None = None, image: str = BASE_IMAGE, enabled: bool = True
    ) -> None:
        self._clock = clock if clock is not None else WallClock()
        self._argv = DockerArgvBuilder()
        self._image = image
        self._docker = shutil.which("docker") or "docker"
        self._control = DockerControl(self._docker)
        self._enabled = enabled  # settings.sandbox.docker_enabled — disabled → unavailable

    # ------------------------------------------------------------- identity
    @property
    def name(self) -> str:
        return "docker"

    @property
    def is_rootless(self) -> bool:
        # The host daemon is rootful — this is the privileged tier (seccomp-gated).
        return False

    @property
    def supports_network(self) -> bool:
        # The network-CAPABLE backend — but network is DENY-by-default (see run()).
        return True

    # ------------------------------------------------------------- availability
    async def is_available(self) -> SandboxAvailability:
        """Daemon reachable AND the base image obtainable. Never raises (B5)."""
        log.tool.debug("[sandbox.docker] is_available: entry")
        if not self._enabled:
            return SandboxAvailability.no(
                "Docker sandbox backend disabled by config (sandbox.docker_enabled=false)"
            )
        try:
            probe = SandboxCapability.probe()
            if not probe.docker_viable:
                return SandboxAvailability.no(probe.docker_reason)
            # Seccomp is mandatory for this rootful tier — can't provision it → not
            # available (it would refuse every run anyway); the selector skips it.
            if SeccompProfile.ensure() is None:
                return SandboxAvailability.no(
                    "Docker daemon is reachable but the mandatory restrictive seccomp "
                    "profile could not be provisioned under ~/.stackowl — refusing to "
                    "run untrusted code without seccomp on a rootful daemon"
                )
            image_ok, reason = await self._control.ensure_image(self._image)
            if not image_ok:
                return SandboxAvailability.no(reason)
            return SandboxAvailability.ok()
        except Exception as exc:  # B5
            log.tool.error("[sandbox.docker] is_available: probe failed", exc_info=exc)
            return SandboxAvailability.no(
                f"Docker availability check failed ({type(exc).__name__})"
            )

    # ------------------------------------------------------------- run
    async def run(self, spec: ExecSpec) -> ExecResult:
        """Run ``spec`` in a hardened container under a wall-time budget. Never raises."""
        started = self._clock.monotonic()
        # 1. ENTRY — shape only, NEVER the code content (length only); no env values.
        log.tool.debug(
            "[sandbox.docker] run: entry",
            extra={
                "_fields": {
                    "code_len": len(spec.code),
                    "network": spec.network,
                    "timeout_s": spec.timeout_s,
                    "session": spec.session_id or "-",
                }
            },
        )
        scratch: Path | None = None
        container = DockerScratch.container_name(spec.session_id)
        try:
            # MANDATORY seccomp (load-bearing on a rootful daemon): refuse if absent.
            profile = SeccompProfile.ensure()
            if profile is None:
                return self._deny(
                    spec, started,
                    "the mandatory restrictive seccomp profile could not be provisioned; "
                    "refusing to run untrusted code without seccomp on a rootful Docker daemon",
                )
            # Image must be present (auto-pull, test-mode-gated) before we run.
            image_ok, reason = await self._control.ensure_image(self._image)
            if not image_ok:
                return self._sandbox_error(spec, started, reason)

            scratch = DockerScratch.make(spec.session_id)
            DockerScratch.write_code(scratch, spec.code)
            argv = self._argv.build(
                spec=spec,
                image=self._image,
                container_name=container,
                code_dir=scratch / "code",
                seccomp_profile=profile,
                docker_bin=self._docker,
            )
            return await self._launch(spec, argv, container, started)
        except Exception as exc:  # B5 — any unexpected failure is structured.
            log.tool.error("[sandbox.docker] run: unexpected failure", exc_info=exc)
            return self._sandbox_error(
                spec, started, f"sandbox setup failed ({type(exc).__name__}: {exc})"
            )
        finally:
            # No --rm (so the OOM inspect reads State.OOMKilled before removal); this
            # force-reap always runs, so no container ever accumulates.
            await self._control.force_remove(container)
            DockerScratch.cleanup(scratch)

    # ------------------------------------------------------------- launch
    async def _launch(
        self, spec: ExecSpec, argv: list[str], container: str, started: float
    ) -> ExecResult:
        """Spawn the hardened container, enforce the timeout, map the outcome."""
        # Audit the hardening flags at debug, but REDACT env VALUES first (an
        # env_allow name could be secret-bearing) → ``--env NAME=***`` (sensitive-data
        # rule). The code lives in the RO-mounted file, never the argv.
        log.tool.debug(
            "[sandbox.docker] _launch: argv built",
            extra={
                "_fields": {
                    "argv": self._argv.redact_env(argv),
                    "container": container,
                    "network": spec.network,
                }
            },
        )
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603 — argv from our builder
                *argv,
                stdin=asyncio.subprocess.PIPE if spec.stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            log.tool.error("[sandbox.docker] _launch: spawn failed", exc_info=exc)
            return self._sandbox_error(
                spec, started, f"failed to launch the container ({type(exc).__name__}: {exc})"
            )

        stdin_bytes = spec.stdin.encode("utf-8") if spec.stdin is not None else None
        timed_out = False
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin_bytes), timeout=spec.timeout_s
            )
        except TimeoutError:
            timed_out = True
            log.tool.info(
                "[sandbox.docker] _launch: wall-time exceeded — killing container",
                extra={"_fields": {"timeout_s": spec.timeout_s, "container": container}},
            )
            await self._control.kill(container)
            out, err = await self._drain(proc)

        return await self._map_result(spec, proc, container, out, err, timed_out, started)

    # ------------------------------------------------------------- result mapping
    async def _map_result(
        self,
        spec: ExecSpec,
        proc: asyncio.subprocess.Process,
        container: str,
        out: bytes,
        err: bytes,
        timed_out: bool,
        started: float,
    ) -> ExecResult:
        """Translate the docker outcome into a provenance-tagged ExecResult."""
        duration = self._elapsed_ms(started)
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        code = proc.returncode

        if timed_out:
            return ExecResult.timed_out(
                stdout=stdout, stderr=stderr, backend_used=self.name,
                network_enabled=spec.network, caps_applied=spec.caps, duration_ms=duration,
            )
        # State.OOMKilled (docker inspect) is authoritative; a 137 without it = non-OOM.
        if await self._control.was_oom_killed(container):
            log.tool.info(
                "[sandbox.docker] _map_result: container OOM-killed",
                extra={"_fields": {"exit_code": code}},
            )
            return ExecResult.error(
                reason="oom",
                message="the run exceeded its memory cap and was OOM-killed",
                backend_used=self.name, caps_applied=spec.caps,
                network_enabled=spec.network, duration_ms=duration,
            )
        if code == _SIGKILL_EXIT or (code is not None and code < 0):
            sig = -code if (code is not None and code < 0) else 9
            log.tool.info(
                "[sandbox.docker] _map_result: container killed by signal",
                extra={"_fields": {"signal": sig, "exit_code": code}},
            )
            return ExecResult.error(
                reason="killed",
                message=f"the run was killed by signal {sig} (resource cap or external kill)",
                backend_used=self.name, caps_applied=spec.caps,
                network_enabled=spec.network, duration_ms=duration,
            )

        log.tool.debug(
            "[sandbox.docker] run: exit",
            extra={"_fields": {"exit_code": code, "duration_ms": duration, "stdout_len": len(stdout)}},
        )
        return ExecResult.ok(
            stdout=stdout, stderr=stderr, exit_code=code if code is not None else -1,
            backend_used=self.name, network_enabled=spec.network,
            caps_applied=spec.caps, duration_ms=duration,
        )

    # ------------------------------------------------------------- util
    @staticmethod
    async def _drain(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        """Collect whatever output a killed run left behind. Never raises."""
        try:
            return await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except (TimeoutError, OSError, ValueError):
            return b"", b""

    def _deny(self, spec: ExecSpec, started: float, message: str) -> ExecResult:
        """A refusal (a guarantee we cannot make) — structured, never raised."""
        return ExecResult.error(
            reason="denied", message=message, backend_used=self.name,
            caps_applied=spec.caps, network_enabled=False,
            duration_ms=self._elapsed_ms(started),
        )

    def _sandbox_error(self, spec: ExecSpec, started: float, message: str) -> ExecResult:
        """A sandbox operational failure — structured, never raised."""
        return ExecResult.error(
            reason="sandbox_error", message=message, backend_used=self.name,
            caps_applied=spec.caps, network_enabled=False,
            duration_ms=self._elapsed_ms(started),
        )

    def _elapsed_ms(self, started: float) -> int:
        return int((self._clock.monotonic() - started) * 1000)
