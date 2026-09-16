"""Covers the "Fresh setup" and "Renewal" I/O matrix rows' printed-output
requirement: the CA fingerprint, guided trust steps, renewal-ceremony text,
and (Story 1.2) the setup code the owner reads from the terminal.
"""

from __future__ import annotations

import kit


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
