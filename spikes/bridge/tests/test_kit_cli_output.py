"""Covers the "Fresh setup" and "Renewal" I/O matrix rows' printed-output
requirement: the CA fingerprint, guided trust steps, renewal-ceremony text,
and (Story 1.2) the setup code the owner reads from the terminal.
"""

from __future__ import annotations

import kit
import pytest


def test_print_trust_steps_includes_fingerprint_and_install_origin(capsys) -> None:  # type: ignore[no-untyped-def]
    kit._print_trust_steps("AA:BB:CC", "example-bridge.local", 8443)
    printed = capsys.readouterr().out
    assert "AA:BB:CC" in printed
    assert "https://example-bridge.local:8443/" in printed


def test_print_renewal_ceremony_tells_the_owner_to_drop_the_old_ca_first(capsys) -> None:  # type: ignore[no-untyped-def]
    kit._print_renewal_ceremony("11:22:33")
    printed = capsys.readouterr().out
    assert "remove the OLD CA" in printed
    assert "11:22:33" in printed


def test_print_setup_code_prints_the_code_to_the_terminal(capsys) -> None:  # type: ignore[no-untyped-def]
    kit._print_setup_code("AB23CD45")
    printed = capsys.readouterr().out
    assert "AB23CD45" in printed


def test_build_parser_routes_verdict_to_cmd_verdict() -> None:
    parser = kit.build_parser()
    args = parser.parse_args(["verdict"])
    assert args.func is kit.cmd_verdict
    # The verdict subcommand never talks to a server: no --name/--port.
    assert not hasattr(args, "name")
    assert not hasattr(args, "port")


def test_build_parser_rejects_name_and_port_on_verdict(capsys) -> None:  # type: ignore[no-untyped-def]
    """The verdict subparser deliberately takes neither flag (it never
    talks to a server) -- argparse must reject both, not silently accept
    and drop them."""
    parser = kit.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["verdict", "--name", "some-install.local"])
    with pytest.raises(SystemExit):
        parser.parse_args(["verdict", "--port", "1234"])


def test_cmd_verdict_prints_the_path_write_verdicts_returns(monkeypatch, tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    written_path = tmp_path / "epic-1-verdicts.md"
    monkeypatch.setattr(kit.verdict, "write_verdicts", lambda: written_path)

    exit_code = kit.main(["verdict"])

    printed = capsys.readouterr().out
    assert exit_code == 0
    assert str(written_path) in printed


def test_main_does_not_crash_on_verdict_subcommand_with_no_name_attribute(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Regression test: main()'s `args.name is None` check used to assume
    every subcommand has a --name attribute. The verdict subparser has
    none, so this must use getattr() rather than crash with AttributeError."""
    written_path = tmp_path / "epic-1-verdicts.md"
    monkeypatch.setattr(kit.verdict, "write_verdicts", lambda: written_path)

    exit_code = kit.main(["verdict"])

    assert exit_code == 0
