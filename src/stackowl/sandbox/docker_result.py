"""classify_docker_outcome — map a finished ``docker run`` to a tagged ExecResult.

Extracted from :mod:`stackowl.sandbox.docker` to keep that backend ≤300 (B2),
mirroring the ``docker_argv`` / ``docker_control`` / ``docker_scratch`` split style.
Pure decision logic (no I/O of its own beyond the already-awaited OOM probe the
caller passes in): given the process result + whether it timed out / was OOM-killed,
it returns the provenance-tagged :class:`~stackowl.sandbox.spec.ExecResult`. The
classification is byte-for-byte what the backend produced inline before the split.
"""

from __future__ import annotations

import asyncio

from stackowl.infra.observability import log
from stackowl.sandbox.spec import ExecResult, ExecSpec

__all__ = ["classify_docker_outcome"]

# Docker's exit code for a SIGKILL'd container (128 + 9); a hint for OOM/killed
# classification alongside the authoritative inspect probe.
_SIGKILL_EXIT = 137


def classify_docker_outcome(
    *,
    spec: ExecSpec,
    proc: asyncio.subprocess.Process,
    out: bytes,
    err: bytes,
    timed_out: bool,
    oom_killed: bool,
    duration_ms: int,
    backend_name: str,
) -> ExecResult:
    """Translate the docker outcome into a provenance-tagged ExecResult."""
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    code = proc.returncode

    if timed_out:
        return ExecResult.timed_out(
            stdout=stdout, stderr=stderr, backend_used=backend_name,
            network_enabled=spec.network, caps_applied=spec.caps, duration_ms=duration_ms,
        )
    # State.OOMKilled (docker inspect) is authoritative; a 137 without it = non-OOM.
    if oom_killed:
        log.tool.info(
            "[sandbox.docker] _map_result: container OOM-killed",
            extra={"_fields": {"exit_code": code}},
        )
        return ExecResult.error(
            reason="oom",
            message="the run exceeded its memory cap and was OOM-killed",
            backend_used=backend_name, caps_applied=spec.caps,
            network_enabled=spec.network, duration_ms=duration_ms,
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
            backend_used=backend_name, caps_applied=spec.caps,
            network_enabled=spec.network, duration_ms=duration_ms,
        )

    log.tool.debug(
        "[sandbox.docker] run: exit",
        extra={"_fields": {"exit_code": code, "duration_ms": duration_ms, "stdout_len": len(stdout)}},
    )
    return ExecResult.ok(
        stdout=stdout, stderr=stderr, exit_code=code if code is not None else -1,
        backend_used=backend_name, network_enabled=spec.network,
        caps_applied=spec.caps, duration_ms=duration_ms,
    )
