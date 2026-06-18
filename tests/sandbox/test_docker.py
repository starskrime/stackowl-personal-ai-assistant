"""Real-isolation tests for DockerSandbox (E11-S2) — actual Docker, benign code.

Integration tests: they run the REAL backend (a real hardened ``docker run``) with
SAFE code and assert the isolation invariants actually hold — network DENIED by
default but grantable on opt-in, the MANDATORY restrictive seccomp profile blocking
a dangerous syscall, a non-root container with no host FS, resource caps (OOM /
pids), and a wall-time kill that leaves NO leftover container. They are BOUNDED
(tiny limits + short timeouts) so a container can never hang the box.

The host's Docker daemon is ROOTFUL here, so the seccomp filter is load-bearing —
the ``test_seccomp_blocks_dangerous_syscall`` case is the tested AC proving an
escape-relevant syscall (``unshare``) is denied (EPERM), not merely "probably off".

If Docker is unreachable in the test env, the real-isolation tests SKIP with an
honest reason; the caps-or-refuse / never-raise / mandatory-seccomp paths are
asserted regardless (they don't need a live daemon). The suite NEVER falsely claims
a guarantee the host can't provide.

Run bounded (never the full suite):

    STACKOWL_HOME=$(mktemp -d) timeout 400 \
        uv run pytest tests/sandbox/test_docker.py -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import asyncio
import json

import pytest

from stackowl.sandbox.docker import DockerSandbox
from stackowl.sandbox.docker_control import DockerControl
from stackowl.sandbox.seccomp import DANGEROUS_SYSCALLS, SeccompProfile
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps

# A short connect probe used by the network tests — bounded so a hung connect can
# never wedge the suite.
_NET_PROBE = """
import socket
try:
    s = socket.create_connection(("1.1.1.1", 53), timeout=4)
    s.close()
    print("CONNECTED")
except OSError as e:
    print("BLOCKED", e.errno)
"""


@pytest.fixture
def sandbox() -> DockerSandbox:
    return DockerSandbox()


@pytest.fixture
async def require_docker(sandbox: DockerSandbox) -> None:
    """Skip the real-isolation tests when Docker is unreachable (honest reason)."""
    avail = await sandbox.is_available()
    if not avail.available:
        pytest.skip(f"Docker sandbox unavailable on this host — {avail.reason}")


# ===================================================================== identity
class TestIdentity:
    def test_backend_identity(self, sandbox: DockerSandbox) -> None:
        assert sandbox.name == "docker"
        # Rootful daemon → NOT rootless (seccomp is the load-bearing control).
        assert sandbox.is_rootless is False
        # The network-CAPABLE tier (deny-by-default, grantable on opt-in).
        assert sandbox.supports_network is True

    async def test_is_available_structured_never_raises(self, sandbox: DockerSandbox) -> None:
        avail = await sandbox.is_available()
        assert isinstance(avail.available, bool)
        if not avail.available:
            assert avail.reason  # an honest human reason when unavailable

    async def test_disabled_by_config_is_unavailable_no_daemon_touch(self) -> None:
        # settings.sandbox.docker_enabled=False → unavailable WITHOUT probing the
        # daemon (short-circuits first), so the selector never offers it.
        avail = await DockerSandbox(enabled=False).is_available()
        assert avail.available is False
        assert "disabled by config" in (avail.reason or "")

    def test_logged_argv_redacts_env_values(self) -> None:
        # A secret a caller forwarded via env_allow must NOT reach the logs: the
        # ``--env NAME=value`` value is masked (name kept) before the argv is logged.
        from stackowl.sandbox.docker_argv import DockerArgvBuilder

        argv = ["docker", "run", "--env", "TOKEN=s3cr3t", "--env", "HOME=/work", "img"]
        red = DockerArgvBuilder.redact_env(argv)
        assert "TOKEN=***" in red and "HOME=***" in red
        assert "s3cr3t" not in " ".join(red)
        # Non-env tokens (the hardening flags) are preserved verbatim for audit.
        assert red[0] == "docker" and red[1] == "run" and red[-1] == "img"


# ================================================================== seccomp file
class TestSeccompProfile:
    """The MANDATORY restrictive profile: default-deny + dangerous syscalls absent."""

    def test_profile_is_default_deny(self) -> None:
        path = SeccompProfile.ensure()
        assert path is not None, "profile must be provisionable"
        prof = json.loads(path.read_text(encoding="utf-8"))
        # Default-deny: anything not explicitly allowed returns an errno.
        assert prof["defaultAction"] == "SCMP_ACT_ERRNO"

    def test_dangerous_syscalls_not_allowlisted(self) -> None:
        path = SeccompProfile.ensure()
        assert path is not None
        prof = json.loads(path.read_text(encoding="utf-8"))
        allowed: set[str] = set()
        for rule in prof["syscalls"]:
            if rule.get("action") == "SCMP_ACT_ALLOW":
                allowed.update(rule.get("names", []))
        leaked = sorted(s for s in DANGEROUS_SYSCALLS if s in allowed)
        assert not leaked, f"dangerous syscalls must NOT be allowlisted: {leaked}"
        # A benign program still needs sockets (network is gated separately).
        assert "socket" in allowed


# ===================================================================== basic run
class TestBasicRun:
    async def test_simple_print(self, sandbox: DockerSandbox, require_docker: None) -> None:
        res = await sandbox.run(ExecSpec(code="print(2 + 2)", timeout_s=30))
        assert res.exit_reason == "ok", res.stderr
        assert res.stdout.strip() == "4"
        assert res.exit_code == 0
        assert res.backend_used == "docker"
        # network_enabled reflects the spec (deny by default).
        assert res.network_enabled is False

    async def test_run_never_raises_on_odd_code(self, sandbox: DockerSandbox) -> None:
        # A run that raises / exits nonzero must still return a structured result.
        res = await sandbox.run(ExecSpec(code="raise SystemExit(3)", timeout_s=20))
        assert isinstance(res, ExecResult)
        assert res.exit_reason in {"ok", "sandbox_error", "denied"}


# ======================================================================= network
class TestNetwork:
    """Invariant #3: deny-by-default; grantable ONLY on explicit opt-in."""

    async def test_network_denied_by_default(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        # network=False (default) → no network namespace egress at all.
        res = await sandbox.run(ExecSpec(code=_NET_PROBE, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        assert res.network_enabled is False
        assert "CONNECTED" not in res.stdout, "network was NOT denied — egress succeeded"
        assert "BLOCKED" in res.stdout

    async def test_network_granted_on_opt_in(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        # network=True → the container is NOT network-namespace-isolated. Proven by
        # the absence of "Network is unreachable" (errno 101 = isolated). A "no route"
        # / DNS failure in an egress-less CI env is acceptable (the tier works); a
        # network-NAMESPACE block is NOT.
        res = await sandbox.run(ExecSpec(code=_NET_PROBE, network=True, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        assert res.network_enabled is True
        assert "BLOCKED 101" not in res.stdout, (
            "container was network-namespace-isolated despite network=True "
            f"(stdout={res.stdout!r})"
        )


# ======================================================================= seccomp
class TestSeccompEnforcement:
    """The tested AC: a dangerous syscall is actually DENIED inside the container."""

    async def test_seccomp_blocks_dangerous_syscall(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        # unshare(CLONE_NEWUSER) is a classic sandbox-escape primitive; the
        # restrictive profile must make it fail with EPERM (errno 1), NOT succeed (0).
        code = (
            "import ctypes, ctypes.util\n"
            "libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)\n"
            "rc = libc.unshare(0x10000000)\n"  # CLONE_NEWUSER
            "print('unshare', rc, ctypes.get_errno())\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        # rc -1 + errno 1 (EPERM) proves seccomp denied the syscall.
        assert "unshare -1 1" in res.stdout, (
            f"seccomp did NOT block unshare (stdout={res.stdout!r}) — "
            "dangerous syscall was not denied"
        )


# ===================================================== filesystem + non-root
class TestFilesystemAndPrivilege:
    """Invariants #6/#7: no host FS, non-root user, read-only rootfs, no docker.sock."""

    async def test_non_root_and_no_host_secrets(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        code = (
            "import os\n"
            "print('uid', os.getuid())\n"
            "try:\n"
            "    open('/etc/shadow').read(); print('shadow', 'READ')\n"
            "except OSError as e:\n"
            "    print('shadow', e.__class__.__name__)\n"
            "print('sock', os.path.exists('/var/run/docker.sock'))\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        # Non-root inside the container (never uid 0).
        assert "uid 65534" in res.stdout
        assert res.stdout.strip().splitlines()[0] != "uid 0"
        # /etc/shadow is unreadable (no host secrets / non-root) — never "READ".
        assert "shadow READ" not in res.stdout
        # The docker socket is NEVER mounted.
        assert "sock False" in res.stdout

    async def test_read_only_rootfs(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        code = (
            "try:\n"
            "    open('/etc/owl_probe', 'w').write('x'); print('WROTE')\n"
            "except OSError as e:\n"
            "    print('readonly', e.errno)\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        assert "WROTE" not in res.stdout, "rootfs was writable — not read-only"
        assert "readonly" in res.stdout


# ========================================================================== caps
class TestResourceCaps:
    """Invariant #2: caps are enforced (bounded + short)."""

    async def test_memory_cap_triggers_oom_or_kill(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        # Balloon well past a tiny 64 MiB cap → the kernel kills it. Classified as
        # oom (authoritative inspect) or killed (a SIGKILL we couldn't attribute).
        res = await sandbox.run(
            ExecSpec(
                code="x = bytearray(500 * 1024 * 1024)\nprint(len(x))",
                timeout_s=20,
                caps=ResourceCaps(mem_mib=64),
            )
        )
        assert res.exit_reason in {"oom", "killed"}, res.stderr
        # It must NOT have completed the allocation as a clean ok.
        assert res.exit_reason != "ok"

    async def test_pids_cap_enforced(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        # A bounded fork attempt past a tiny pids cap fails inside (the program sees
        # the limit) — the run completes (ok) and reports it hit the wall, NOT a
        # host-level fork bomb. Bounded loop, never unbounded.
        code = (
            "import os, sys\n"
            "n = 0\n"
            "try:\n"
            "    for _ in range(64):\n"
            "        pid = os.fork()\n"
            "        if pid == 0:\n"
            "            import time; time.sleep(1); os._exit(0)\n"
            "        n += 1\n"
            "except OSError as e:\n"
            "    print('FORK_LIMIT', e.errno)\n"
            "else:\n"
            "    print('FORKED', n)\n"
        )
        res = await sandbox.run(
            ExecSpec(code=code, timeout_s=20, caps=ResourceCaps(pids=16))
        )
        # Either the program hit the pids wall (FORK_LIMIT) or the run was killed —
        # never an unbounded success creating 64 host processes.
        assert res.exit_reason in {"ok", "killed", "oom"}, res.stderr
        if res.exit_reason == "ok":
            assert "FORK_LIMIT" in res.stdout, (
                f"pids cap not enforced — forked freely (stdout={res.stdout!r})"
            )


# ======================================================================= timeout
class TestTimeout:
    async def test_timeout_kills_and_reaps(
        self, sandbox: DockerSandbox, require_docker: None
    ) -> None:
        control = DockerControl(sandbox._docker)  # noqa: SLF001 — test reaches in to verify reaping
        res = await sandbox.run(ExecSpec(code="import time; time.sleep(30)", timeout_s=2))
        assert res.exit_reason == "timeout", res.stderr
        # No leftover stackowl sandbox container after a timeout kill. `docker rm -f`
        # has run, but `docker ps -a` can lag it by a sub-second eventual-consistency
        # window under load — poll (bounded) for the container to vanish, don't race it.
        out = "unchecked"
        for _ in range(25):  # ~5s max
            ok, out = await control.run(
                ["ps", "-a", "--filter", "name=stackowl-sbx-", "--format", "{{.Names}}"]
            )
            assert ok
            if out.strip() == "":
                break
            await asyncio.sleep(0.2)
        assert out.strip() == "", f"leftover container(s) after timeout: {out!r}"


# ============================================================== caps-or-refuse
class TestCapsOrRefuseAndSeccompMandatory:
    """Refusal paths that hold WITHOUT a live daemon (mocked failures)."""

    async def test_refuses_when_seccomp_unprovisionable(
        self, sandbox: DockerSandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the mandatory seccomp profile can't be written, the run is DENIED —
        # never executed unconfined on the rootful daemon.
        monkeypatch.setattr(SeccompProfile, "ensure", classmethod(lambda cls: None))
        res = await sandbox.run(ExecSpec(code="print(1)", timeout_s=10))
        assert res.exit_reason == "denied"
        assert res.network_enabled is False
        assert res.backend_used == "docker"
        assert "seccomp" in res.stderr.lower()

    async def test_sandbox_error_when_image_unobtainable(
        self, sandbox: DockerSandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A profile is fine but the image cannot be obtained → structured
        # sandbox_error, never a run.
        async def _no_image(self: DockerControl, image: str) -> tuple[bool, str]:
            return False, "image unobtainable in this test"

        monkeypatch.setattr(DockerControl, "ensure_image", _no_image)
        res = await sandbox.run(ExecSpec(code="print(1)", timeout_s=10))
        assert res.exit_reason == "sandbox_error"
        assert "image" in res.stderr.lower()

    async def test_unavailable_when_daemon_down(
        self, sandbox: DockerSandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Daemon-down probe → is_available is a structured no, never raises.
        from stackowl.sandbox import docker as docker_mod
        from stackowl.sandbox.capability import SandboxProbe

        down = SandboxProbe(
            bwrap_viable=False,
            docker_viable=False,
            bwrap_reason="n/a",
            docker_reason="daemon unreachable (test)",
            platform_supported=True,
        )
        monkeypatch.setattr(
            docker_mod.SandboxCapability, "probe", classmethod(lambda cls: down)
        )
        avail = await sandbox.is_available()
        assert avail.available is False
        assert avail.reason and "unreachable" in avail.reason
