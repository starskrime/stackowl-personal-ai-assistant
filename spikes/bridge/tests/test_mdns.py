"""Unit tests for bridge_spike.mdns: the exact argv built for each OS, and
the never-crash fallbacks when an address or the mDNS tool itself is missing.

The core thing under test is the argv shape: both `avahi-publish-service`
and `dns-sd` must be invoked in their ADDRESS-publishing mode (so
`<install_name>` itself becomes resolvable), never their bare
service-registration mode (which only advertises a service under the host's
own existing hostname and leaves `<install_name>` unresolvable).
"""

from __future__ import annotations

import subprocess

from bridge_spike import mdns

INSTALL_NAME = "test-mdns.local"
PORT = 8443
ADDRESS = "192.168.1.42"


def test_build_command_linux_publishes_an_address_not_a_bare_service() -> None:
    command = mdns.build_command(INSTALL_NAME, PORT, ADDRESS, system="Linux")
    assert command[0] == "avahi-publish-service"
    assert "-a" in command, "must use avahi-publish-service's address-publish mode (-a)"
    assert "-R" in command, "must skip the reverse/PTR record to avoid a Local name collision"
    assert INSTALL_NAME in command
    assert ADDRESS in command
    # The bare service-registration shape (`avahi-publish-service <name> <type> <port>`,
    # no -a) is exactly the mode that was found NOT to make <install_name> resolvable.
    assert mdns.SERVICE_TYPE not in command


def test_build_command_darwin_uses_proxy_record_mode() -> None:
    command = mdns.build_command(INSTALL_NAME, PORT, ADDRESS, system="Darwin")
    assert command[0] == "dns-sd"
    assert "-P" in command, "must use dns-sd's proxy-record mode (-P), not bare -R"
    assert "-R" not in command
    assert INSTALL_NAME in command
    assert ADDRESS in command
    assert str(PORT) in command
    assert mdns.SERVICE_TYPE in command


def test_build_command_unsupported_platform_raises() -> None:
    try:
        mdns.build_command(INSTALL_NAME, PORT, ADDRESS, system="Windows")
    except mdns.UnsupportedPlatformError:
        pass
    else:
        raise AssertionError("expected UnsupportedPlatformError")


def test_start_advertising_with_no_discovered_address_does_not_crash(monkeypatch, capsys) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.Popen must not be called when address is None")

    monkeypatch.setattr(subprocess, "Popen", fail_if_called)

    advertisement = mdns.start_advertising(INSTALL_NAME, PORT, None)

    assert advertisement.process is None
    assert advertisement.running is False
    printed = capsys.readouterr().out
    assert INSTALL_NAME in printed
    assert "avahi-publish-service" in printed  # the remedy command is still printed


def test_start_advertising_missing_tool_prints_remedy_instead_of_crashing(monkeypatch, capsys) -> None:
    def raise_not_found(*args, **kwargs):
        raise FileNotFoundError("no such file: avahi-publish-service")

    monkeypatch.setattr(subprocess, "Popen", raise_not_found)

    advertisement = mdns.start_advertising(INSTALL_NAME, PORT, ADDRESS, system="Linux")

    assert advertisement.process is None
    assert advertisement.running is False
    printed = capsys.readouterr().out
    assert "avahi-publish-service" in printed
    assert ADDRESS in printed


def test_start_advertising_success_returns_a_running_advertisement(monkeypatch) -> None:
    class FakeProcess:
        def poll(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProcess())

    advertisement = mdns.start_advertising(INSTALL_NAME, PORT, ADDRESS, system="Linux")

    assert advertisement.running is True
    assert advertisement.command[0] == "avahi-publish-service"


def test_stop_reaps_the_process_after_a_kill(monkeypatch) -> None:
    """A process that ignores terminate() and has to be kill()ed must still
    be wait()ed on afterwards, or it is left as an unreaped zombie."""
    calls: list[str] = []

    class HangingProcess:
        def poll(self) -> None:
            return None if "killed" not in calls else 0

        def terminate(self) -> None:
            calls.append("terminate")

        def wait(self, timeout: float | None = None) -> int:
            if "killed" not in calls:
                raise subprocess.TimeoutExpired(cmd="mdns", timeout=timeout or 0)
            calls.append("waited-after-kill")
            return 0

        def kill(self) -> None:
            calls.append("killed")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: HangingProcess())

    advertisement = mdns.start_advertising(INSTALL_NAME, PORT, ADDRESS, system="Linux")
    advertisement.stop()

    assert calls == ["terminate", "killed", "waited-after-kill"]
