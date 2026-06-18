"""DockerControl — the bounded docker control-plane calls for the Docker backend.

Split out of :mod:`stackowl.sandbox.docker` (B2 ≤300) so the backend body stays
focused on the run lifecycle while the daemon-facing primitives live here: the
one-time image pull (test-mode-gated), image presence, the OOM-kill inspect probe,
and the timeout-kill / force-reap. Every method is bounded (a hung daemon must not
wedge a run) and NEVER raises — each returns a structured signal the backend maps
to an :class:`~stackowl.sandbox.spec.ExecResult` (B5 self-healing).
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from stackowl.config.test_mode import TestModeGuard
from stackowl.infra.observability import log

__all__ = ["DockerControl"]

# Bounded timeout for the one-time image pull (slow first run); a hung pull must
# not wedge a run. The pull only happens once (image is cached after).
_PULL_TIMEOUT_S = 600.0
# Bounded timeouts for the short control-plane commands (inspect / kill / rm).
_CONTROL_TIMEOUT_S = 15.0


class DockerControl:
    """Bounded, never-raising docker control-plane calls. Construct with the bin."""

    def __init__(self, docker_bin: str) -> None:
        self._docker = docker_bin

    # ------------------------------------------------------------- image
    async def ensure_image(self, image: str) -> tuple[bool, str]:
        """Ensure ``image`` is present locally, pulling once if missing.

        Returns ``(ok, reason)``. The pull is gated by :class:`TestModeGuard` so NO
        docker pull ever runs in test mode (mirrors the tts/image auto-install
        guard). Never raises (B5).
        """
        if await self.image_present(image):
            return True, ""
        try:
            # No real pull under test mode — gate BEFORE the network/daemon call.
            TestModeGuard.assert_not_test_mode("docker.image.pull")
        except Exception as exc:  # TestModeViolation (or any) → structured, not raised.
            log.tool.debug(
                "[sandbox.docker] ensure_image: pull blocked",
                extra={"_fields": {"reason": str(exc)}},
            )
            return False, f"base image '{image}' is absent and cannot be pulled here ({exc})"
        log.tool.info(
            "[sandbox.docker] ensure_image: pulling base image (first use)",
            extra={"_fields": {"image": image}},
        )
        ok, detail = await self.run(["pull", image], timeout=_PULL_TIMEOUT_S)
        if not ok:
            return False, (
                f"base image '{image}' could not be pulled ({detail or 'docker pull failed'})"
            )
        return True, ""

    async def image_present(self, image: str) -> bool:
        """True iff ``image`` already exists locally. Never raises."""
        ok, out = await self.run(["image", "inspect", image, "--format", "{{.Id}}"])
        return ok and bool(out.strip())

    # ------------------------------------------------------------- container
    async def was_oom_killed(self, container: str) -> bool:
        """Read ``State.OOMKilled`` for the (just-exited) container. Never raises.

        The container is run WITHOUT ``--rm`` so it still exists at inspect time;
        should it nonetheless be absent (a daemon hiccup), this reads as "not OOM"
        and the backend's exit-code heuristic still distinguishes a kill. Structured.
        """
        ok, out = await self.run(
            ["inspect", container, "--format", "{{json .State.OOMKilled}}"]
        )
        if not ok:
            return False
        try:
            return json.loads(out.strip() or "false") is True
        except (ValueError, json.JSONDecodeError):
            return False

    async def kill(self, container: str) -> None:
        """SIGKILL the running container on timeout. Best-effort, never raises."""
        await self.run(["kill", container])

    async def force_remove(self, container: str) -> None:
        """Force-remove the container (the guaranteed reaper; no --rm). Never raises."""
        await self.run(["rm", "-f", container])

    # ------------------------------------------------------------- primitive
    async def run(self, args: list[str], *, timeout: float = _CONTROL_TIMEOUT_S) -> tuple[bool, str]:
        """Run a short docker control command. Returns ``(ok, stdout)``. Never raises."""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(  # noqa: S603 — fixed argv
                self._docker, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (OSError, TimeoutError, ValueError) as exc:
            log.tool.debug(
                "[sandbox.docker] control: command failed",
                extra={"_fields": {"args": args[:2], "err": type(exc).__name__}},
            )
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
            return False, ""
        if proc.returncode != 0:
            return False, err.decode("utf-8", errors="replace").strip()
        return True, out.decode("utf-8", errors="replace")
