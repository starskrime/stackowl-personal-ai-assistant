"""Tests for the shared store_secret helper (keychain + file fallback)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest


def _seed_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path / "home"))


class TestStoreSecret:
    def test_keychain_ref_when_keyring_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_home(tmp_path, monkeypatch)
        from stackowl.config.secret_writer import store_secret

        captured: dict[str, str] = {}

        class _FakeKeyring:
            @staticmethod
            def set_password(service: str, user: str, secret: str) -> None:
                captured.update(service=service, user=user, secret=secret)

        monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)

        description, ref = store_secret("stackowl-provider-acme", "RAW-123")
        assert ref == "keychain:stackowl-provider-acme"
        assert description == "OS keyring"
        # keyring received the raw secret with matching service/user
        assert captured == {
            "service": "stackowl-provider-acme",
            "user": "stackowl-provider-acme",
            "secret": "RAW-123",
        }

    def test_file_ref_mode_0600_when_keyring_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_home(tmp_path, monkeypatch)
        from stackowl.config.secret_writer import store_secret

        class _BrokenKeyring:
            @staticmethod
            def set_password(service: str, user: str, secret: str) -> None:
                raise RuntimeError("no backend available")

        monkeypatch.setitem(sys.modules, "keyring", _BrokenKeyring)

        description, ref = store_secret("stackowl-provider-acme", "RAW-456")
        assert ref.startswith("file:")
        assert description == ref
        secret_path = Path(ref[len("file:") :])
        assert secret_path.exists()
        assert secret_path.read_text(encoding="utf-8") == "RAW-456"
        # mode-0600 (owner read/write only) on POSIX
        if sys.platform != "win32":
            mode = stat.S_IMODE(secret_path.stat().st_mode)
            assert mode == 0o600

    def test_secret_resolver_reads_back_file_ref(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_home(tmp_path, monkeypatch)
        from stackowl.config.secret_resolver import SecretResolver
        from stackowl.config.secret_writer import store_secret

        class _BrokenKeyring:
            @staticmethod
            def set_password(service: str, user: str, secret: str) -> None:
                raise RuntimeError("no backend")

        monkeypatch.setitem(sys.modules, "keyring", _BrokenKeyring)

        _desc, ref = store_secret("stackowl-provider-roundtrip", "ROUNDTRIP-SECRET")
        assert SecretResolver.resolve(ref) == "ROUNDTRIP-SECRET"

    def test_secret_resolver_reads_back_keychain_ref(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_home(tmp_path, monkeypatch)
        from stackowl.config.secret_resolver import SecretResolver
        from stackowl.config.secret_writer import store_secret

        store: dict[tuple[str, str], str] = {}

        class _FakeKeyring:
            @staticmethod
            def set_password(service: str, user: str, secret: str) -> None:
                store[(service, user)] = secret

            @staticmethod
            def get_password(service: str, user: str) -> str | None:
                return store.get((service, user))

        monkeypatch.setitem(sys.modules, "keyring", _FakeKeyring)

        _desc, ref = store_secret("stackowl-provider-kc", "KC-SECRET")
        assert ref == "keychain:stackowl-provider-kc"
        assert SecretResolver.resolve(ref) == "KC-SECRET"
