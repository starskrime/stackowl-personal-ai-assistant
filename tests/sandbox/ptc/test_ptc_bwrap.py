"""REAL-bwrap PTC integration — sandboxed code calls back to host tools over the UDS.

These run ACTUAL code inside a REAL rootless bwrap sandbox (no network) with the PTC
channel enabled, and assert the load-bearing end-to-end facts:

* an ALLOWED tool (read_file) called via ``import owl`` from inside the sandbox runs
  on the HOST and the result comes back INTO the sandbox;
* a HARD-EXCLUDED tool (owl.shell) is refused (clean error in-sandbox; host never ran it);
* the write tools are confined to the sandbox workspace;
* the network stays DENIED even with PTC enabled (the socket is the only channel).

SKIP-with-reason when bwrap / cgroup-v2 delegation is unavailable. Bounded timeouts.

Run bounded:
    STACKOWL_HOME=$(mktemp -d) timeout 300 \
      uv run pytest tests/sandbox/ptc/test_ptc_bwrap.py -p no:cacheprovider -o addopts="" -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.sandbox.bwrap import BwrapSandbox
from stackowl.sandbox.ptc.server import PtcServer
from stackowl.sandbox.spec import ExecResult, ExecSpec


# --- a tiny REAL host tool surface for the callback ------------------------------


class _ToolResult:
    def __init__(self, *, success: bool, output: str = "", error: str | None = None) -> None:
        self.success = success
        self.output = output
        self.error = error


class _RealReadFile:
    """A minimal real read_file: reads from an absolute host path the test seeds."""

    async def execute(self, **kwargs: object) -> _ToolResult:
        try:
            return _ToolResult(success=True, output=Path(str(kwargs["path"])).read_text())
        except Exception as exc:  # noqa: BLE001
            return _ToolResult(success=False, error=str(exc))


class _Registry:
    def __init__(self, tools: dict[str, object]) -> None:
        self._tools = tools

    def get(self, name: str) -> object | None:
        return self._tools.get(name)


@pytest.fixture
def sandbox() -> BwrapSandbox:
    return BwrapSandbox()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


@pytest.fixture
async def require_sandbox(sandbox: BwrapSandbox) -> None:
    avail = await sandbox.is_available()
    if not avail.available:
        pytest.skip(f"bwrap sandbox unavailable — {avail.reason}")


def _factory(registry: object, host_secret: Path):  # noqa: ANN202
    def _make(workspace: Path, socket_path: Path) -> PtcServer:
        return PtcServer(
            registry=registry, workspace=workspace,
            socket_path=socket_path, session_id="ptc-it",
        )
    return _make


@pytest.mark.usefixtures("require_sandbox")
class TestRealPtcBwrap:
    async def test_allowed_callback_runs_on_host_and_returns(
        self, sandbox: BwrapSandbox, tmp_path: Path
    ) -> None:
        secret = tmp_path / "host_data.txt"
        secret.write_text("HOST-TOOL-RAN-42")
        registry = _Registry({"read_file": _RealReadFile()})
        code = (
            "import owl\n"
            f"print('GOT:' + owl.read_file({str(secret)!r}))\n"
        )
        res: ExecResult = await sandbox.run(
            ExecSpec(code=code, timeout_s=20), ptc_factory=_factory(registry, secret)
        )
        assert res.exit_reason == "ok", res.stderr
        assert "GOT:HOST-TOOL-RAN-42" in res.stdout, res.stdout

    async def test_hard_excluded_shell_refused_in_sandbox(
        self, sandbox: BwrapSandbox, tmp_path: Path
    ) -> None:
        ran = tmp_path / "should_not_exist.txt"
        registry = _Registry({"read_file": _RealReadFile()})
        code = (
            "import owl\n"
            "try:\n"
            "    owl.shell(command='touch " + str(ran) + "')\n"
            "    print('SHELL_RAN')\n"
            "except Exception as e:\n"
            "    print('REFUSED:' + str(e))\n"
        )
        res = await sandbox.run(
            ExecSpec(code=code, timeout_s=20), ptc_factory=_factory(registry, tmp_path)
        )
        assert res.exit_reason == "ok", res.stderr
        assert "SHELL_RAN" not in res.stdout, "the sandbox reached shell — exclusion breached"
        assert "REFUSED:" in res.stdout
        assert "not callable from a sandbox" in res.stdout
        assert not ran.exists(), "the excluded shell tool actually ran on the host"

    async def test_network_still_denied_with_ptc_on(
        self, sandbox: BwrapSandbox, tmp_path: Path
    ) -> None:
        registry = _Registry({"read_file": _RealReadFile()})
        code = (
            "import owl, socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=3); print('CONNECTED')\n"
            "except Exception as e:\n"
            "    print('NETFAIL', type(e).__name__)\n"
        )
        res = await sandbox.run(
            ExecSpec(code=code, timeout_s=20), ptc_factory=_factory(registry, tmp_path)
        )
        assert res.exit_reason == "ok", res.stderr
        assert "CONNECTED" not in res.stdout, "network was NOT denied with PTC on"
        assert "NETFAIL" in res.stdout

    async def test_no_ptc_factory_means_no_owl_module(
        self, sandbox: BwrapSandbox
    ) -> None:
        # Without a factory the stub is NOT injected — import owl fails (unchanged path).
        code = "import owl\nprint('IMPORTED')\n"
        res = await sandbox.run(ExecSpec(code=code, timeout_s=20))
        assert res.exit_reason == "ok", res.stderr
        assert "IMPORTED" not in res.stdout
        assert "ModuleNotFoundError" in res.stderr or "No module named" in res.stderr
