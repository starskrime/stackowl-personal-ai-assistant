"""cgroup-v2 resource caging for a sandboxed run (caps-or-REFUSE, invariant #2).

A sandboxed run is NEVER executed uncapped. On a rootless Linux host the only way
to enforce memory/pids ceilings on an unprivileged process is a delegated
cgroup-v2 subtree, obtained via ``systemd-run --user --scope -p Delegate=yes``
(the standard rootless delegation path — systemd creates a scope cgroup OWNED by
the calling user).

Architecture (verified on a real host): the run is launched AS the scope payload.
``systemd-run`` runs a small ``/bin/sh`` whose recipe, inside the freshly-delegated
scope, (1) creates a ``leader`` leaf + a ``run`` leaf, (2) moves itself into
``leader`` so the scope root is process-free (the cgroup-v2 "no internal processes"
rule — a cgroup with member processes cannot enable controllers in its subtree),
(3) enables ``+memory +pids`` in the scope's ``subtree_control``, (4) writes
``memory.max`` / ``memory.swap.max`` / ``pids.max`` (and ``cpu.max`` when the cpu
controller is delegated) into ``run``, (5) records the ``run`` cgroup path to a
host-visible marker so the backend can read ``memory.events`` afterwards, (6) moves
ITSELF into ``run``, and (7) ``exec``s bwrap — which therefore inherits the capped
cgroup. If ANY mandatory step fails the recipe exits with a distinct non-zero code
and the run is REFUSED — never uncapped.

Delegation reality (honest): a typical user session delegates ``memory`` + ``pids``
but NOT ``cpu`` (delegating cpu to a user slice needs host configuration). The
recipe HARD-enforces memory.max (+ swap=0: OOM host-DoS is the threat) and pids.max
(the fork-bomb rail); it applies cpu.max only when delegated, otherwise CPU is
bounded by the MANDATORY wall-time kill (the backend) — surfaced, never silent.

OOM detection: after the run, the ``run`` cgroup's ``memory.events`` ``oom_kill``
counter > 0 means the kernel SIGKILLed a member for breaching ``memory.max`` → the
backend reports ``exit_reason="oom"``.

This module builds the recipe + the ``systemd-run`` argv and reads the OOM marker.
It spawns NOTHING itself (the backend owns the subprocess). Never raises (B5).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.sandbox.spec import ResourceCaps

__all__ = ["CgroupRecipe"]

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_MIB = 1024 * 1024

# Distinct exit codes the recipe uses for each mandatory setup failure, so the
# backend can report *which* cap could not be enforced when it REFUSES.
EXIT_MKDIR = 71
EXIT_LEADER = 72
EXIT_SUBTREE = 73
EXIT_MEMORY = 74
EXIT_PIDS = 75
EXIT_JOIN = 76
_REFUSE_EXITS = {EXIT_MKDIR, EXIT_LEADER, EXIT_SUBTREE, EXIT_MEMORY, EXIT_PIDS, EXIT_JOIN}

# Name of the host-visible marker file (under the run's scratch) the recipe writes
# the delegated ``run`` cgroup path into, so the backend can inspect memory.events.
CGROUP_MARKER = ".cgroup_path"


class CgroupRecipe:
    """Builds the delegated-scope shell recipe + systemd-run argv. Never raises."""

    @classmethod
    def delegation_available(cls) -> tuple[bool, str]:
        """Probe whether a delegated cgroup-v2 subtree is obtainable. Never raises.

        Confirms the unified cgroup-v2 hierarchy is mounted AND ``systemd-run`` is on
        PATH (the rootless delegation mechanism). The live recipe additionally
        REFUSES if a controller fails to delegate, so this is a fast pre-check for
        the backend's ``is_available``.
        """
        try:
            if not (_CGROUP_ROOT / "cgroup.controllers").exists():
                return False, (
                    "cgroup-v2 unified hierarchy not found at /sys/fs/cgroup — resource "
                    "caps cannot be enforced; bwrap will not run uncapped"
                )
            if shutil.which("systemd-run") is None:
                return False, (
                    "'systemd-run' not found — no rootless cgroup-v2 delegation path to "
                    "enforce memory/pids caps; bwrap will not run uncapped"
                )
            return True, "cgroup-v2 delegation via systemd-run available"
        except OSError as exc:  # B5 — unknown → unavailable
            return False, f"cgroup-v2 delegation probe failed ({type(exc).__name__})"

    @classmethod
    def build_command(
        cls, *, caps: ResourceCaps, unit: str, marker_path: Path, bwrap_argv: list[str]
    ) -> list[str]:
        """Return the full ``systemd-run`` argv that caps + launches ``bwrap_argv``.

        The returned argv, when spawned, ends up ``exec``ing the given bwrap command
        inside a memory/pids-capped delegated cgroup. stdin/stdout/stderr of the
        bwrap process are proxied through ``systemd-run`` to the caller.
        """
        # 1. ENTRY
        log.tool.debug(
            "[sandbox.cgroup] build_command: entry",
            extra={"_fields": {"unit": unit, "mem_mib": caps.mem_mib, "pids": caps.pids}},
        )
        recipe = cls._recipe(caps=caps, marker_path=marker_path, bwrap_argv=bwrap_argv)
        argv = [
            "systemd-run", "--user", "--scope", "--quiet",
            "--unit", unit, "-p", "Delegate=yes",
            "/bin/sh", "-c", recipe,
        ]
        # 4. EXIT
        log.tool.debug("[sandbox.cgroup] build_command: exit", extra={"_fields": {"argv_len": len(argv)}})
        return argv

    @classmethod
    def _recipe(cls, *, caps: ResourceCaps, marker_path: Path, bwrap_argv: list[str]) -> str:
        """The /bin/sh recipe: delegate, cap, record marker, join, exec bwrap."""
        mem_bytes = caps.mem_mib * _MIB
        cpu_quota = caps.cpu_cores * 100_000  # cpu.max: <quota> per 100000us window
        exec_line = "exec " + " ".join(_shq(a) for a in bwrap_argv)
        marker = _shq(str(marker_path))
        # NOTE: cpu.max is best-effort (written only if the file exists, i.e. the cpu
        # controller is delegated); memory.max + pids.max are mandatory (distinct
        # non-zero exit on failure → the backend REFUSES rather than run uncapped).
        return (
            'P=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); '
            f'mkdir -p "$P/leader" "$P/run" || exit {EXIT_MKDIR}; '
            f'echo $$ > "$P/leader/cgroup.procs" || exit {EXIT_LEADER}; '
            f'echo "+memory +pids" > "$P/cgroup.subtree_control" || exit {EXIT_SUBTREE}; '
            f'echo "{mem_bytes}" > "$P/run/memory.max" || exit {EXIT_MEMORY}; '
            'echo "0" > "$P/run/memory.swap.max" 2>/dev/null; '
            f'echo "{caps.pids}" > "$P/run/pids.max" || exit {EXIT_PIDS}; '
            f'[ -e "$P/run/cpu.max" ] && echo "{cpu_quota} 100000" > "$P/run/cpu.max" 2>/dev/null; '
            f'printf "%s" "$P/run" > {marker}; '
            f'echo $$ > "$P/run/cgroup.procs" || exit {EXIT_JOIN}; '
            f"{exec_line}"
        )

    @staticmethod
    def is_refusal_exit(code: int | None) -> bool:
        """True if ``code`` is one of the recipe's caps-could-not-be-enforced codes."""
        return code in _REFUSE_EXITS

    @staticmethod
    def refusal_message(code: int | None) -> str:
        """Human-readable explanation for a recipe REFUSE exit code."""
        mapping = {
            EXIT_MKDIR: "could not create the delegated cgroup directories",
            EXIT_LEADER: "could not move the launcher into the leader leaf",
            EXIT_SUBTREE: "could not delegate the memory/pids controllers to the subtree",
            EXIT_MEMORY: "could not write the memory cap (memory.max)",
            EXIT_PIDS: "could not write the process cap (pids.max)",
            EXIT_JOIN: "could not move the run into the capped cgroup",
        }
        detail = mapping.get(code or -1, "cgroup setup failed")
        return f"could not enforce mandatory resource caps — {detail}; run refused (never run uncapped)"

    @staticmethod
    def oom_killed(marker_path: Path) -> bool:
        """True if the run's cgroup OOM-killed a member. Reads the marker. Never raises."""
        try:
            run_dir = Path(marker_path.read_text().strip())
        except OSError:
            return False
        try:
            events = (run_dir / "memory.events").read_text()
        except OSError:
            return False
        for line in events.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "oom_kill":
                try:
                    return int(parts[1]) > 0
                except ValueError:
                    return False
        return False


def _shq(arg: str) -> str:
    """POSIX single-quote shell-escape (the recipe runs under /bin/sh -c)."""
    return "'" + arg.replace("'", "'\\''") + "'"
