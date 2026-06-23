"""Wiring seams for the two-process split (orchestrator role + entrypoints).

Covers the small, pure seams the orchestrator's role-conditional `_phase_gateway`
hangs off — role validation, socket-path precedence, the supervisor's core-spawn
command, and the hidden `__core__` CLI registration. The full split round-trip is
already proven by tests/runtime/test_split_link.py over a real socket; these guard
the assembly glue that selects mono vs gateway vs core.
"""

from __future__ import annotations

import sys

import pytest

from stackowl.config.settings import Settings
from stackowl.startup.orchestrator import StartupOrchestrator, _resolve_socket_path


def test_default_role_is_mono() -> None:
    assert StartupOrchestrator()._role == "mono"


@pytest.mark.parametrize("role", ["mono", "gateway", "core"])
def test_valid_roles_accepted(role: str) -> None:
    assert StartupOrchestrator(role=role)._role == role


def test_invalid_role_rejected() -> None:
    with pytest.raises(ValueError, match="invalid orchestrator role"):
        StartupOrchestrator(role="bogus")


def test_socket_path_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STACKOWL_CORE_SOCKET", "/tmp/explicit-core.sock")
    settings = Settings()
    assert str(_resolve_socket_path(settings)) == "/tmp/explicit-core.sock"


def test_socket_path_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STACKOWL_CORE_SOCKET", raising=False)
    settings = Settings()
    # runtime is frozen; rebuild it with the override (env-less path).
    runtime = settings.runtime.model_copy(update={"socket_path": "/tmp/cfg.sock"})
    settings = settings.model_copy(update={"runtime": runtime})
    assert str(_resolve_socket_path(settings)) == "/tmp/cfg.sock"


def test_socket_path_defaults_to_home_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STACKOWL_CORE_SOCKET", raising=False)
    settings = Settings()
    assert settings.runtime.socket_path is None
    path = _resolve_socket_path(settings)
    assert path.name == "core.sock"
    assert path.parent.name == "runtime"


async def test_spawn_core_builds_core_subcommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn_core re-enters the CLI at __core__ with the socket in the env."""
    from stackowl.runtime import supervisor

    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 4242
        returncode = None

    async def _fake_exec(*args: str, env: dict[str, str], **_: object) -> _FakeProc:
        captured["args"] = args
        captured["env"] = env
        return _FakeProc()

    monkeypatch.setattr(supervisor.asyncio, "create_subprocess_exec", _fake_exec)

    proc = await supervisor.spawn_core("/tmp/sock-here.sock")

    assert proc.pid == 4242
    args = captured["args"]
    assert args[0] == sys.executable
    assert args[1:] == ("-m", "stackowl", "__core__")
    assert captured["env"]["STACKOWL_CORE_SOCKET"] == "/tmp/sock-here.sock"


def test_core_cli_command_is_registered_and_hidden() -> None:
    """The hidden __core__ entrypoint exists so the gateway can spawn the core."""
    from stackowl.cli.app import app

    cmds = {c.name: c for c in app.registered_commands}
    assert "__core__" in cmds
    assert cmds["__core__"].hidden is True
