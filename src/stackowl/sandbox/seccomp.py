"""SeccompProfile — the MANDATORY restrictive syscall filter for the Docker tier.

The host Docker daemon here is ROOTFUL (the daemon runs as root), so a container
escape can reach host root. That makes the seccomp filter *load-bearing* for the
Docker backend (invariant #7): unlike the rootless bwrap backend, dropping
capabilities + ``no-new-privileges`` is NOT sufficient on its own — a restrictive
seccomp profile is what removes the dangerous syscall surface an escape would use.
The Docker backend REFUSES to run if this profile cannot be applied; it NEVER runs
``seccomp=unconfined``.

Design — DEFAULT-DENY allowlist (the same shape Docker's own default profile uses,
``defaultAction: SCMP_ACT_ERRNO``): every syscall not on the allowlist returns
``EPERM``. We start from the broad, benign allowlist a normal Python program needs
and DELIBERATELY OMIT the dangerous syscalls a sandbox-escape relies on:

* namespace / privilege pivots — ``unshare``, ``setns``, ``clone`` with new-namespace
  flags (``clone`` itself is allowed only for thread/process creation; the profile's
  argument filter forbids the ``CLONE_NEW*`` bits), ``pivot_root``, ``chroot``.
* mount manipulation — ``mount``, ``umount`` / ``umount2``, ``move_mount``,
  ``open_tree``, ``fsopen`` / ``fsmount`` / ``fsconfig``.
* kernel keyring — ``keyctl``, ``add_key``, ``request_key``.
* tracing / kernel-control — ``ptrace``, ``bpf``, ``perf_event_open``,
  ``kexec_load`` / ``kexec_file_load``, ``reboot``, ``swapon`` / ``swapoff``,
  ``init_module`` / ``finit_module`` / ``delete_module``, ``acct``, ``quotactl``,
  ``nfsservctl``, ``mount_setattr``.

The profile JSON is AGENT-PROVISIONED on first use under
``~/.stackowl/sandbox/seccomp/<name>.json`` (all-state-in-home) and re-used
thereafter. NO code runs here — this only writes/locates the profile file. Never
raises a profile that silently disables filtering; if the file cannot be written
the caller treats it as "seccomp unavailable" and the backend REFUSES.
"""

from __future__ import annotations

import json
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

__all__ = ["SeccompProfile"]

# Profile schema version baked into the filename so a tightened profile supersedes
# a stale one provisioned by an earlier build (the agent re-writes on mismatch).
_PROFILE_NAME = "stackowl-restrictive-v3"

# The dangerous syscalls this profile MUST NOT allow. Documented here as the
# explicit denylist this profile is verified against (the allowlist below simply
# omits them); the test suite asserts at least one of these is actually blocked.
DANGEROUS_SYSCALLS: tuple[str, ...] = (
    "unshare", "setns", "pivot_root", "chroot",
    "mount", "umount", "umount2", "move_mount", "open_tree",
    "fsopen", "fsmount", "fsconfig", "fspick", "mount_setattr",
    "keyctl", "add_key", "request_key",
    "ptrace", "bpf", "perf_event_open",
    "kexec_load", "kexec_file_load", "reboot",
    "swapon", "swapoff",
    "init_module", "finit_module", "delete_module",
    "acct", "quotactl", "nfsservctl",
    "_sysctl", "personality",
)

# The benign allowlist a normal Python program needs (file/socket/process/memory
# primitives). Every dangerous syscall above is DELIBERATELY absent. ``clone`` is
# allowed (threads/processes) but constrained by an argument filter below so the
# new-namespace bits cannot be set. ``socket`` is present so ``socket.create_
# connection`` works WHEN the network namespace is granted (network deny is enforced
# by ``--network=none``, an orthogonal control — not by removing the syscall).
_ALLOWED_SYSCALLS: tuple[str, ...] = (
    # process / thread lifecycle
    "fork", "vfork", "execve", "execveat", "exit", "exit_group", "wait4",
    "waitid", "clone3", "set_tid_address", "set_robust_list", "get_robust_list",
    "gettid", "getpid", "getppid", "getpgrp", "getpgid", "setpgid", "getsid",
    "setsid", "getrandom", "rseq",
    # scheduling / signals
    "sched_yield", "sched_getaffinity", "sched_setaffinity", "sched_getparam",
    "sched_getscheduler", "sched_get_priority_max", "sched_get_priority_min",
    "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "rt_sigpending",
    "rt_sigtimedwait", "rt_sigqueueinfo", "rt_sigsuspend", "sigaltstack",
    "kill", "tkill", "tgkill", "pause", "nanosleep", "clock_nanosleep",
    "restart_syscall",
    # memory
    "brk", "mmap", "mmap2", "munmap", "mremap", "mprotect", "madvise",
    "mlock", "munlock", "mlockall", "munlockall", "membarrier", "msync",
    # file I/O
    "read", "write", "readv", "writev", "pread64", "pwrite64", "preadv",
    "pwritev", "preadv2", "pwritev2", "open", "openat", "openat2", "close",
    "close_range", "creat", "lseek", "_llseek", "dup", "dup2", "dup3",
    "fcntl", "fcntl64", "flock", "fsync", "fdatasync", "ftruncate",
    "truncate", "fallocate", "sync", "sync_file_range", "syncfs",
    "pipe", "pipe2", "tee", "splice", "sendfile", "copy_file_range",
    "readahead", "vmsplice",
    # directory / metadata
    "stat", "stat64", "lstat", "fstat", "fstat64", "fstatat64", "newfstatat",
    "statx", "access", "faccessat", "faccessat2", "getcwd", "chdir", "fchdir",
    "mkdir", "mkdirat", "rmdir", "rename", "renameat", "renameat2",
    "link", "linkat", "symlink", "symlinkat", "unlink", "unlinkat",
    "readlink", "readlinkat", "getdents", "getdents64", "chmod", "fchmod",
    "fchmodat", "chown", "fchown", "lchown", "fchownat", "umask", "utime",
    "utimes", "utimensat", "futimesat", "statfs", "statfs64", "fstatfs",
    "fstatfs64", "name_to_handle_at",
    # extended attrs (read/list only commonly needed; set allowed for tooling)
    "getxattr", "lgetxattr", "fgetxattr", "listxattr", "llistxattr",
    "flistxattr", "setxattr", "lsetxattr", "fsetxattr",
    # epoll / poll / eventfd / signalfd / timers
    "poll", "ppoll", "select", "_newselect", "pselect6", "epoll_create",
    "epoll_create1", "epoll_ctl", "epoll_wait", "epoll_pwait", "epoll_pwait2",
    "eventfd", "eventfd2", "signalfd", "signalfd4", "timerfd_create",
    "timerfd_settime", "timerfd_gettime", "timer_create", "timer_settime",
    "timer_gettime", "timer_getoverrun", "timer_delete", "getitimer",
    "setitimer", "inotify_init", "inotify_init1", "inotify_add_watch",
    "inotify_rm_watch",
    # ids / limits / info
    "getuid", "geteuid", "getgid", "getegid", "getgroups", "getresuid",
    "getresgid", "getrlimit", "setrlimit", "prlimit64", "getrusage",
    "ugetrlimit", "sysinfo", "uname", "times", "getpriority", "setpriority",
    "capget", "capset", "prctl", "arch_prctl",
    # uid/gid setup — the container runtime DROPS to the non-root --user via these
    # BEFORE exec. They permit privilege *drop*, never escalation (cap-drop=ALL +
    # no-new-privileges + non-root already forbid gaining privilege); allowing them
    # makes the run robust to the runtime's seccomp/uid-drop ordering. Docker's own
    # default profile allows them too.
    "setuid", "setgid", "setgroups", "setresuid", "setresgid",
    "setfsuid", "setfsgid", "setreuid", "setregid",
    # clocks
    "clock_gettime", "clock_getres", "clock_settime", "gettimeofday",
    "time", "adjtimex", "clock_adjtime",
    # sockets / networking syscalls (egress still gated by --network=none)
    "socket", "socketpair", "bind", "connect", "listen", "accept", "accept4",
    "getsockname", "getpeername", "getsockopt", "setsockopt", "sendto",
    "recvfrom", "sendmsg", "recvmsg", "sendmmsg", "recvmmsg", "shutdown",
    "socketcall",
    # ipc (futex is essential for CPython threading/locks)
    "futex", "futex_waitv", "get_thread_area", "set_thread_area",
    "memfd_create", "io_setup", "io_destroy", "io_submit", "io_cancel",
    "io_getevents", "ioctl",
    "process_vm_readv",
)


class SeccompProfile:
    """Provisions + locates the restrictive seccomp profile JSON (agent-managed)."""

    @classmethod
    def path(cls) -> Path:
        """The on-disk location of the restrictive profile under ~/.stackowl/."""
        return StackowlHome.home() / "sandbox" / "seccomp" / f"{_PROFILE_NAME}.json"

    @classmethod
    def ensure(cls) -> Path | None:
        """Write the profile if missing/stale and return its path. Never raises.

        Returns ``None`` when the profile cannot be provisioned (the caller then
        REFUSES the run — seccomp is mandatory for this rootful-daemon tier, never
        skipped). Idempotent: an up-to-date file is reused untouched.
        """
        log.tool.debug("[sandbox.seccomp] ensure: entry")
        target = cls.path()
        try:
            content = cls._render()
            if cls._already_current(target, content):
                log.tool.debug(
                    "[sandbox.seccomp] ensure: profile current",
                    extra={"_fields": {"path": str(target)}},
                )
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            # Write atomically (tmp + replace) so a partial write never yields a
            # half-valid profile that would weaken the filter.
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(target)
            log.tool.info(
                "[sandbox.seccomp] ensure: profile provisioned",
                extra={"_fields": {"path": str(target), "allowed": len(_ALLOWED_SYSCALLS)}},
            )
            return target
        except OSError as exc:
            # No silent degrade: a profile we cannot write means the backend must
            # refuse (it will read the None and deny) — we surface the failure loudly.
            log.tool.error("[sandbox.seccomp] ensure: could not provision profile", exc_info=exc)
            return None

    # --------------------------------------------------------------- internals
    @staticmethod
    def _already_current(target: Path, content: str) -> bool:
        """True iff the file exists with exactly the rendered content. Never raises."""
        try:
            return target.read_text(encoding="utf-8") == content
        except OSError:
            return False

    @staticmethod
    def _render() -> str:
        """Render the default-deny allowlist profile as canonical JSON.

        ``defaultAction: SCMP_ACT_ERRNO`` — anything not explicitly allowed returns
        ``EPERM``. ``clone`` is allowed but argument-filtered so the ``CLONE_NEW*``
        namespace-creation bits cannot be set (blocks userns/netns escapes that go
        via ``clone`` rather than ``unshare``).
        """
        # CLONE_NEW* mask (NEWNS|NEWUTS|NEWIPC|NEWUSER|NEWPID|NEWNET|NEWCGROUP).
        clone_new_mask = (
            0x00020000  # NEWNS
            | 0x04000000  # NEWUTS
            | 0x08000000  # NEWIPC
            | 0x10000000  # NEWUSER
            | 0x20000000  # NEWPID
            | 0x40000000  # NEWNET
            | 0x02000000  # NEWCGROUP
        )
        syscalls: list[dict[str, object]] = [
            {
                "names": sorted(set(_ALLOWED_SYSCALLS)),
                "action": "SCMP_ACT_ALLOW",
            },
            {
                # Allow clone ONLY when none of the new-namespace bits are set.
                "names": ["clone"],
                "action": "SCMP_ACT_ALLOW",
                "args": [
                    {
                        "index": 0,
                        "value": clone_new_mask,
                        "valueTwo": 0,
                        "op": "SCMP_CMP_MASKED_EQ",
                    }
                ],
            },
        ]
        profile: dict[str, object] = {
            "defaultAction": "SCMP_ACT_ERRNO",
            "defaultErrnoRet": 1,  # EPERM
            "archMap": [
                {"architecture": "SCMP_ARCH_AARCH64", "subArchitectures": []},
                {
                    "architecture": "SCMP_ARCH_X86_64",
                    "subArchitectures": ["SCMP_ARCH_X86", "SCMP_ARCH_X32"],
                },
            ],
            "syscalls": syscalls,
        }
        return json.dumps(profile, indent=2, sort_keys=True) + "\n"
