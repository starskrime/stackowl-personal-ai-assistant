"""SandboxCapability — the host PROBE that decides WHICH backends are viable.

Code execution needs OS-level isolation primitives that exist only on Linux in
practice: a rootless user-namespace sandbox (bubblewrap, ``bwrap``) and/or a
container daemon (Docker / Podman, the ``docker`` CLI). This probe detects which
of those are present and usable BEFORE the selector commits to a backend, so an
incapable host (non-Linux, or Linux without either tool) degrades to a structured
"no sandbox available" rather than crashing or — far worse — silently running
code on the bare host.

Conservative-by-design ([[feedback_always_self_healing]] / B5): the probe NEVER
raises, and anything it cannot positively confirm is treated as UNAVAILABLE
("unknown → unavailable", mirroring the image capability probe's
"unknown → safe-default"). A binary that is present but errors on its
version/info check is NOT considered viable.

NO sandbox is created here and NO code is run — this only inspects the host
(``shutil.which`` + a bounded version/info probe). The result drives the
selector's bwrap-primary policy (E11-S1 selector).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

from stackowl.infra.observability import log

__all__ = ["SandboxCapability", "SandboxProbe"]

# Bounded timeout for the probe subprocesses — a hung daemon must not wedge the
# probe. The calls are read-only version/info checks.
_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class SandboxProbe:
    """The host's sandbox capability verdict — which backends are viable, and why.

    ``bwrap_viable`` / ``docker_viable`` are positive confirmations only (a tool
    the probe could not confirm is False). ``bwrap_reason`` / ``docker_reason``
    carry a human-readable explanation for the verdict (present or why-not), and
    ``platform_supported`` records whether the host OS can host a sandbox at all.
    """

    bwrap_viable: bool
    docker_viable: bool
    bwrap_reason: str
    docker_reason: str
    platform_supported: bool

    @property
    def any_viable(self) -> bool:
        """True when at least one backend can isolate a run on this host."""
        return self.bwrap_viable or self.docker_viable


class SandboxCapability:
    """Probes the host for usable sandbox backends. Never raises (B5)."""

    @classmethod
    def probe(cls) -> SandboxProbe:
        """Detect viable sandbox backends on this host. Never raises.

        Linux-only in practice: on a non-Linux host neither primitive is usable,
        so the probe returns both-unavailable with a structured reason rather than
        attempting (and failing) a Linux-specific check. "unknown → unavailable".
        """
        # 1. ENTRY
        log.tool.debug("[sandbox.capability] probe: entry")
        try:
            system = platform.system()
            if system != "Linux":
                # 2. DECISION — non-Linux: no rootless userns / no host daemon path
                #    we will trust. Structured unavailable, never a crash.
                reason = (
                    f"sandboxing requires Linux isolation primitives; host OS is "
                    f"'{system or 'unknown'}' — no sandbox backend is viable here"
                )
                log.tool.info(
                    "[sandbox.capability] probe: non-Linux host → no backend viable",
                    extra={"_fields": {"system": system}},
                )
                return SandboxProbe(
                    bwrap_viable=False,
                    docker_viable=False,
                    bwrap_reason=reason,
                    docker_reason=reason,
                    platform_supported=False,
                )

            bwrap_viable, bwrap_reason = cls._probe_bwrap()
            docker_viable, docker_reason = cls._probe_docker()

            # 4. EXIT
            log.tool.info(
                "[sandbox.capability] probe: exit",
                extra={
                    "_fields": {
                        "bwrap_viable": bwrap_viable,
                        "docker_viable": docker_viable,
                    }
                },
            )
            return SandboxProbe(
                bwrap_viable=bwrap_viable,
                docker_viable=docker_viable,
                bwrap_reason=bwrap_reason,
                docker_reason=docker_reason,
                platform_supported=True,
            )
        except Exception as exc:  # any probe error → unknown → both unavailable.
            log.tool.error("[sandbox.capability] probe: unexpected failure", exc_info=exc)
            reason = (
                f"sandbox capability could not be determined "
                f"({type(exc).__name__}) — treating all backends as unavailable"
            )
            return SandboxProbe(
                bwrap_viable=False,
                docker_viable=False,
                bwrap_reason=reason,
                docker_reason=reason,
                platform_supported=False,
            )

    # --------------------------------------------------------------- detection
    @classmethod
    def _probe_bwrap(cls) -> tuple[bool, str]:
        """bubblewrap present + a working ``bwrap --version``. Never raises.

        Presence alone is not enough — the binary must answer its version probe
        (a broken / non-executable ``bwrap`` is not viable). The deeper rootless
        userns enforcement is left to the backend's own ``is_available`` (S3); the
        probe confirms the primitive EXISTS and runs.
        """
        path = shutil.which("bwrap")
        if path is None:
            return False, (
                "bubblewrap ('bwrap') is not installed — install it for rootless, "
                "daemonless sandboxing (e.g. 'apt install bubblewrap')"
            )
        ran = cls._run_probe([path, "--version"])
        if ran is None:
            return False, (
                f"bubblewrap is present at {path} but its '--version' probe failed "
                f"— treating it as not viable"
            )
        return True, f"bubblewrap available ({ran})"

    @classmethod
    def _probe_docker(cls) -> tuple[bool, str]:
        """docker CLI present + a reachable daemon (``docker version``). Never raises.

        The CLI being on PATH is not enough — the daemon must answer. ``docker
        version`` exits non-zero when the daemon is unreachable, which the probe
        treats as not viable (a network-requiring run would then have nowhere to
        go, and the selector says so).
        """
        path = shutil.which("docker")
        if path is None:
            return False, (
                "Docker CLI is not installed — install Docker (or Podman) for "
                "network-capable sandboxing"
            )
        ran = cls._run_probe([path, "version", "--format", "{{.Server.Version}}"])
        if ran is None:
            return False, (
                f"Docker CLI is present at {path} but the daemon is unreachable "
                f"('docker version' failed) — start the Docker daemon to enable it"
            )
        return True, f"Docker available (server {ran})"

    @staticmethod
    def _run_probe(argv: list[str]) -> str | None:
        """Run a bounded, read-only probe command. Returns trimmed stdout or None.

        None signals "could not confirm" (missing binary handled by the caller,
        non-zero exit, timeout, or any OS error) → the caller treats it as not
        viable. Never raises.
        """
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, read-only probe.
                argv,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.tool.debug(
                "[sandbox.capability] probe command failed",
                extra={"_fields": {"argv0": argv[0], "err": type(exc).__name__}},
            )
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or "ok"
