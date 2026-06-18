"""PLUG-1 + PLUG-2 — plugin index checksum schema + verified remote install.

PLUG-1: the plugin index entry carries an integrity ``sha256`` field (tolerant of
legacy entries that omit it — idempotent). The installed-plugins table records the
verified hash (migration 0063).

PLUG-2: ``PluginVerifier`` computes the sha256 of a downloaded archive and compares
it (constant-time) against the index entry's expected digest. It NEVER imports or
executes the downloaded bytes — verification is pure hashing. A mismatch (or a
missing expected digest) is REFUSED, fail-closed. Code only ever runs later via the
consent-gated local-install + serve-boot path, never as a side effect of download.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from stackowl.plugins.index import PluginIndex, PluginIndexEntry
from stackowl.plugins.verify import PluginVerificationError, PluginVerifier


# --------------------------------------------------------------------------- #
# PLUG-1 — index entry carries sha256, legacy entries tolerated.
# --------------------------------------------------------------------------- #
def test_index_entry_has_sha256_field() -> None:
    e = PluginIndexEntry(
        name="x", url="https://e/x.zip", version="1.0.0",
        description="d", type="local_plugin", sha256="abc123",
    )
    assert e.sha256 == "abc123"


def test_index_parses_sha256_and_tolerates_legacy(tmp_path: Path) -> None:
    idx_file = tmp_path / "plugin-index.yaml"
    idx_file.write_text(
        "newp:\n"
        "  url: https://e/newp.zip\n"
        "  version: 2.0.0\n"
        "  description: signed\n"
        "  type: local_plugin\n"
        "  sha256: deadbeef\n"
        "legacyp:\n"  # legacy entry with NO sha256 — must still parse
        "  url: https://e/legacy.zip\n"
        "  version: 1.0.0\n"
        "  description: unsigned legacy\n",
        encoding="utf-8",
    )
    idx = PluginIndex(index_path=idx_file)
    assert idx.lookup("newp").sha256 == "deadbeef"
    assert idx.lookup("legacyp").sha256 == ""  # absent → empty, no crash


# --------------------------------------------------------------------------- #
# PLUG-2 — verifier hashes and compares; never executes; fail-closed.
# --------------------------------------------------------------------------- #
def test_verifier_accepts_matching_digest(tmp_path: Path) -> None:
    blob = b"plugin archive bytes"
    f = tmp_path / "p.zip"
    f.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()

    verifier = PluginVerifier()
    # Returns the verified digest; does NOT raise, does NOT import anything.
    assert verifier.verify_file(f, expected_sha256=digest) == digest


def test_verifier_refuses_mismatch(tmp_path: Path) -> None:
    f = tmp_path / "p.zip"
    f.write_bytes(b"tampered bytes")

    verifier = PluginVerifier()
    with pytest.raises(PluginVerificationError) as ei:
        verifier.verify_file(f, expected_sha256="0" * 64)
    assert "checksum" in str(ei.value).lower() or "digest" in str(ei.value).lower()


def test_verifier_refuses_missing_expected_digest(tmp_path: Path) -> None:
    f = tmp_path / "p.zip"
    f.write_bytes(b"bytes")

    verifier = PluginVerifier()
    # No expected digest → cannot verify → fail closed (NEVER install unverified).
    with pytest.raises(PluginVerificationError):
        verifier.verify_file(f, expected_sha256="")


def test_verifier_is_case_insensitive_hex(tmp_path: Path) -> None:
    blob = b"abc"
    f = tmp_path / "p.zip"
    f.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest().upper()  # uppercase hex from index
    verifier = PluginVerifier()
    assert verifier.verify_file(f, expected_sha256=digest).lower() == digest.lower()
