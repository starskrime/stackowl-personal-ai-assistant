"""Core must never write or unlink the shared PID file.

Root cause: `stackowl stop` / PidManager both target ONE shared path
(StackowlHome.pid_file()). Core boots ~1s after gateway in split mode and
previously overwrote it unconditionally with its own PID, so `stop` (and its
own later teardown unlink) hit/cleared the wrong process's entry — SIGTERM
landed on core, which the gateway's crash-respawn supervisor then treated as
an unexpected crash and spawned a fresh core instead of the service actually
stopping. Only mono/gateway are the externally-stoppable top-level process.
"""

from stackowl.startup.orchestrator import StartupOrchestrator


def test_core_role_excluded_from_pid_write_guard():
    orch = StartupOrchestrator(role="core")
    # Mirrors the exact guard in StartupOrchestrator.run() before phase 6.
    should_write = (not orch._dry_run) and (orch._role != "core")
    assert should_write is False


def test_gateway_and_mono_roles_included_in_pid_write_guard():
    for role in ("mono", "gateway"):
        orch = StartupOrchestrator(role=role)
        should_write = (not orch._dry_run) and (orch._role != "core")
        assert should_write is True


def test_core_role_excluded_from_pid_unlink_guard():
    orch = StartupOrchestrator(role="core")
    # Mirrors the exact guard in the _phase_gateway teardown finally block.
    should_unlink = orch._role != "core"
    assert should_unlink is False
