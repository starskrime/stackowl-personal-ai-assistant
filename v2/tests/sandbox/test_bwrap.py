"""Real-isolation tests for BwrapSandbox (E11-S3) — actual bwrap, benign code.

These are integration tests: they run the REAL backend (real bwrap inside a real
delegated cgroup) with SAFE code, and assert the isolation invariants actually
hold — network denied, host secrets invisible, resource caps enforced, env
scrubbed, wall-time killed. They are BOUNDED (tiny limits + short timeouts) so a
sandbox can never hang the box.

If the host cannot provide a sandbox (no bwrap, or no cgroup-v2 delegation), the
isolation tests SKIP with an honest reason and the caps-or-refuse path is asserted
instead — the suite never falsely claims a guarantee the host can't provide.

Run bounded (never the full suite):

    STACKOWL_HOME=$(mktemp -d) timeout 300 \
        uv run pytest tests/sandbox/test_bwrap.py -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import os

import pytest

from stackowl.sandbox.bwrap import BwrapSandbox
from stackowl.sandbox.cgroup import (
    EXIT_MEMORY,
    EXIT_PIDS,
    CgroupRecipe,
)
from stackowl.sandbox.spec import ExecResult, ExecSpec, ResourceCaps


@pytest.fixture
def sandbox() -> BwrapSandbox:
    return BwrapSandbox()


@pytest.fixture
async def require_sandbox(sandbox: BwrapSandbox) -> None:
    """Skip the real-isolation tests when the host can't sandbox (honest reason)."""
    avail = await sandbox.is_available()
    if not avail.available:
        pytest.skip(f"bwrap sandbox unavailable on this host — {avail.reason}")


# --------------------------------------------------------------------- caps math
class TestStructuredContract:
    """is_available is structured + never raises; malformed-ish specs are handled."""

    async def test_is_available_structured(self, sandbox: BwrapSandbox) -> None:
        avail = await sandbox.is_available()
        # available is a bool; when not available a human reason is present.
        assert isinstance(avail.available, bool)
        if not avail.available:
            assert avail.reason

    async def test_disabled_by_config_is_unavailable(self) -> None:
        # settings.sandbox.bwrap_enabled=False → unavailable, short-circuits the probe.
        avail = await BwrapSandbox(enabled=False).is_available()
        assert avail.available is False
        assert "disabled by config" in (avail.reason or "")

    async def test_network_request_refused_not_run(self, sandbox: BwrapSandbox) -> None:
        # invariant #3: a network=True spec to this no-network backend is REFUSED
        # (denied), never silently run — independent of host sandbox availability.
        res = await sandbox.run(ExecSpec(code="print(1)", network=True))
        assert isinstance(res, ExecResult)
        assert res.exit_reason == "denied"
        assert res.network_enabled is False
        assert res.backend_used == "bwrap"

    async def test_run_never_raises_on_odd_code(self, sandbox: BwrapSandbox) -> None:
        # A run that raises Python errors must still return a structured result,
        # never propagate an exception out of run().
        res = await sandbox.run(ExecSpec(code="raise SystemExit(3)", timeout_s=10))
        assert isinstance(res, ExecResult)
        # Either a real isolated run (exit_reason ok, nonzero code) or a structured
        # unavailable refusal — but NEVER a raise.
        assert res.exit_reason in {"ok", "sandbox_error"}


# ------------------------------------------------------------- real isolation
@pytest.mark.usefixtures("require_sandbox")
class TestRealIsolation:
    """Run actual bwrap with benign code and assert the invariants hold."""

    async def test_basic_run_ok(self, sandbox: BwrapSandbox) -> None:
        res = await sandbox.run(ExecSpec(code="print(2 + 2)", timeout_s=15))
        assert res.exit_reason == "ok", res.stderr
        assert res.exit_code == 0
        assert res.stdout.strip() == "4"
        assert res.backend_used == "bwrap"
        assert res.network_enabled is False

    async def test_network_denied(self, sandbox: BwrapSandbox) -> None:
        # invariant #3: no network namespace egress — a DNS connect must FAIL, not
        # succeed. We assert the program reports the failure (non-zero / error),
        # NOT a successful connection.
        code = (
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
            "    print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print('NETFAIL', type(e).__name__)\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=15))
        assert res.exit_reason == "ok", res.stderr
        assert "CONNECTED" not in res.stdout, "network was NOT denied — egress succeeded"
        assert "NETFAIL" in res.stdout

    async def test_no_host_fs_secret_access(self, sandbox: BwrapSandbox) -> None:
        # invariant #6: host secret files are not mounted — opening them fails.
        code = (
            "import os\n"
            "targets = ['/etc/shadow', os.path.expanduser('~/.stackowl/.secrets/x'), '/etc/passwd']\n"
            "for t in targets:\n"
            "    try:\n"
            "        open(t).read(); print('READ', t)\n"
            "    except Exception as e:\n"
            "        print('NOACCESS', t, type(e).__name__)\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=15))
        assert res.exit_reason == "ok", res.stderr
        assert "READ /etc/shadow" not in res.stdout
        assert "READ /etc/passwd" not in res.stdout
        assert "NOACCESS /etc/shadow" in res.stdout

    async def test_env_scrubbed(self, sandbox: BwrapSandbox) -> None:
        # invariant #4: a secret env var set on the host is NOT visible inside.
        os.environ["STACKOWL_TEST_SECRET"] = "topsecret-should-not-cross"
        try:
            code = "import os; print('SECRET=', os.environ.get('STACKOWL_TEST_SECRET'))"
            res = await sandbox.run(ExecSpec(code=code, timeout_s=15))
        finally:
            os.environ.pop("STACKOWL_TEST_SECRET", None)
        assert res.exit_reason == "ok", res.stderr
        assert "SECRET= None" in res.stdout
        assert "topsecret" not in res.stdout

    async def test_workspace_is_writable(self, sandbox: BwrapSandbox) -> None:
        # The scratch /workspace IS writable (the only writable mount) and HOME==/workspace.
        code = (
            "import os\n"
            "open('out.txt', 'w').write('hi')\n"
            "print('HOME=', os.environ.get('HOME'))\n"
            "print('WROTE', open('out.txt').read())\n"
        )
        res = await sandbox.run(ExecSpec(code=code, timeout_s=15))
        assert res.exit_reason == "ok", res.stderr
        assert "HOME= /workspace" in res.stdout
        assert "WROTE hi" in res.stdout

    async def test_memory_cap_enforced_oom(self, sandbox: BwrapSandbox) -> None:
        # invariant #2: a memory balloon far past a tiny cap is OOM-killed by the
        # cgroup (NOT the host). BOUNDED: 32 MiB cap, short timeout.
        caps = ResourceCaps(mem_mib=32, cpu_cores=1, pids=64, wall_time_s=10, fs_write_mib=16)
        code = "x = bytearray(400 * 1024 * 1024); print(len(x))"
        res = await sandbox.run(ExecSpec(code=code, caps=caps, timeout_s=15))
        # The balloon must NOT print its length; the run is killed for memory.
        assert "419430400" not in res.stdout
        assert res.exit_reason in {"oom", "killed"}, f"expected oom/killed, got {res.exit_reason}: {res.stderr}"

    async def test_pids_cap_enforced(self, sandbox: BwrapSandbox) -> None:
        # invariant #2: a (bounded) fork attempt past the pids cap fails inside the
        # sandbox — the host is unaffected. BOUNDED: small pids cap + bounded loop.
        caps = ResourceCaps(mem_mib=128, cpu_cores=1, pids=16, wall_time_s=10, fs_write_mib=16)
        code = (
            "import os, time\n"
            "kids = 0\n"
            "for _ in range(200):\n"
            "    try:\n"
            "        pid = os.fork()\n"
            "    except OSError:\n"
            "        print('PIDCAP_HIT'); break\n"
            "    if pid == 0:\n"
            "        time.sleep(2); os._exit(0)\n"
            "    kids += 1\n"
            "else:\n"
            "    print('NO_PIDCAP', kids)\n"
        )
        res = await sandbox.run(ExecSpec(code=code, caps=caps, timeout_s=15))
        # Either the fork cap was hit (EAGAIN) or the run was killed — never an
        # unbounded fork that prints NO_PIDCAP with a large count.
        assert "NO_PIDCAP" not in res.stdout, f"pids cap not enforced: {res.stdout}"

    async def test_timeout_killed_promptly(self, sandbox: BwrapSandbox) -> None:
        # A 30s sleep with a 2s timeout → exit_reason timeout, killed well under 30s.
        res = await sandbox.run(ExecSpec(code="import time; time.sleep(30)", timeout_s=2))
        assert res.exit_reason == "timeout", res.stderr
        assert res.duration_ms < 20_000, f"kill was not prompt: {res.duration_ms}ms"


class TestCapsOrRefuse:
    """The caps-or-refuse path (invariant #2) — tested even on a capable host."""

    async def test_unavailable_when_no_cgroup_delegation(
        self, sandbox: BwrapSandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If cgroup-v2 delegation cannot enforce caps, is_available() must say NO —
        # never report a host where code would run uncapped as available.
        monkeypatch.setattr(
            "stackowl.sandbox.bwrap.CgroupRecipe.delegation_available",
            classmethod(lambda _cls: (False, "no delegated subtree")),
        )
        avail = await sandbox.is_available()
        assert avail.available is False
        assert avail.reason and "cannot enforce mandatory caps" in avail.reason

    async def test_run_refuses_when_delegation_fails(
        self, sandbox: BwrapSandbox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate the recipe REFUSING (a mandatory cap could not be written): the
        # run returns a structured sandbox_error, NEVER an uncapped ok.
        def _fake_build(**_kw: object) -> list[str]:
            return ["/bin/sh", "-c", f"exit {EXIT_MEMORY}"]

        monkeypatch.setattr(
            "stackowl.sandbox.bwrap.CgroupRecipe.build_command",
            classmethod(lambda _cls, **kw: _fake_build(**kw)),
        )
        res = await sandbox.run(ExecSpec(code="print(1)", timeout_s=10))
        assert res.exit_reason == "sandbox_error"
        assert "could not enforce mandatory resource caps" in res.stderr
        assert res.exit_code is None


class TestRecipeMapping:
    """Unit coverage for the recipe's refusal-code mapping (no host needed)."""

    def test_refusal_exit_classification(self) -> None:
        assert CgroupRecipe.is_refusal_exit(EXIT_MEMORY)
        assert CgroupRecipe.is_refusal_exit(EXIT_PIDS)
        assert not CgroupRecipe.is_refusal_exit(0)
        assert not CgroupRecipe.is_refusal_exit(1)  # a program's own nonzero exit
        assert not CgroupRecipe.is_refusal_exit(None)

    def test_refusal_messages_explain_the_cap(self) -> None:
        assert "memory" in CgroupRecipe.refusal_message(EXIT_MEMORY).lower()
        assert "process cap" in CgroupRecipe.refusal_message(EXIT_PIDS).lower()
