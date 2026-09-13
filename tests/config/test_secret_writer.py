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


class TestLocatedSecrets:
    """A record whose ABSENCE can be told apart from an unreadable store (Q29).

    MEASURED on the dev box: the keyring backend raises `KeyringLocked` on every
    call. So "the keyring raised" alone cannot mean "unreadable" — the locator the
    write leaves is what says whether the record lives there at all.
    """

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_home(tmp_path, monkeypatch)

    @staticmethod
    def _keyring(monkeypatch: pytest.MonkeyPatch, **impl: object) -> None:
        import keyring
        import keyring.errors

        def _absent(*_a: object, **_k: object) -> None:
            raise keyring.errors.NoKeyringError("no backend")

        for name in ("get_password", "set_password", "delete_password"):
            monkeypatch.setattr(keyring, name, impl.get(name, _absent))

    @staticmethod
    def _file(service: str) -> Path:
        from stackowl.paths import StackowlHome

        return StackowlHome.secrets_dir() / f"{service}.key"

    @staticmethod
    def _locked(*_a: object, **_k: object) -> None:
        import keyring.errors

        raise keyring.errors.KeyringLocked("Failed to unlock the collection!")

    def test_a_FILE_backed_record_is_its_own_locator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from stackowl.config.secret_writer import (
            delete_secret,
            read_located_secret,
            store_located_secret,
        )

        self._keyring(monkeypatch)
        assert read_located_secret("stackowl-q29") is None

        store_located_secret("stackowl-q29", "RECORD")
        assert read_located_secret("stackowl-q29") == "RECORD"

        delete_secret("stackowl-q29")
        assert read_located_secret("stackowl-q29") is None
        delete_secret("stackowl-q29")  # idempotent

    def test_a_KEYRING_record_leaves_a_locator_and_is_read_from_the_keyring(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import (
            delete_secret,
            read_located_secret,
            store_located_secret,
        )

        vault: dict[str, str] = {}
        self._keyring(
            monkeypatch,
            get_password=lambda s, _u: vault.get(s),
            set_password=lambda s, _u, v: vault.__setitem__(s, v),
            delete_password=lambda s, _u: vault.pop(s),
        )

        store_located_secret("stackowl-q29", "RECORD")
        assert self._file("stackowl-q29").read_text(encoding="utf-8") == "keychain:stackowl-q29"
        assert "RECORD" not in self._file("stackowl-q29").read_text(encoding="utf-8")
        assert read_located_secret("stackowl-q29") == "RECORD"

        delete_secret("stackowl-q29")
        assert vault == {}
        assert not self._file("stackowl-q29").exists()

    def test_a_LOCKED_keyring_that_holds_the_record_is_UNREADABLE_not_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import (
            SecretStoreUnreadable,
            read_located_secret,
            store_located_secret,
        )

        vault: dict[str, str] = {}
        self._keyring(monkeypatch, set_password=lambda s, _u, v: vault.__setitem__(s, v))
        store_located_secret("stackowl-q29", "RECORD")
        self._keyring(monkeypatch, get_password=self._locked)

        with pytest.raises(SecretStoreUnreadable):
            read_located_secret("stackowl-q29")

    def test_a_locked_keyring_with_NO_locator_is_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import read_located_secret, store_located_secret

        self._keyring(monkeypatch, get_password=self._locked, set_password=self._locked)

        assert read_located_secret("stackowl-q29") is None
        store_located_secret("stackowl-q29", "RECORD")
        assert read_located_secret("stackowl-q29") == "RECORD"

    def test_a_locator_whose_keyring_entry_is_GONE_reads_as_damaged(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import read_located_secret

        self._keyring(monkeypatch, get_password=lambda _s, _u: None)
        self._file("stackowl-q29").parent.mkdir(parents=True, exist_ok=True)
        self._file("stackowl-q29").write_text("keychain:stackowl-q29", encoding="utf-8")

        assert read_located_secret("stackowl-q29") == ""

    def test_an_unreadable_FILE_is_unreadable_not_absent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import SecretStoreUnreadable, read_located_secret

        self._keyring(monkeypatch)
        self._file("stackowl-q29").mkdir(parents=True)

        with pytest.raises(SecretStoreUnreadable):
            read_located_secret("stackowl-q29")

    def test_a_rewrite_never_exposes_a_TRUNCATED_record(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A reader racing a rewrite must see the old record or the new one, never an
        empty file — the per-request password lookup reads that as DAMAGED."""
        import os

        from stackowl.config import secret_writer as mod

        self._keyring(monkeypatch)
        mod.store_located_secret("stackowl-q29", "OLD-RECORD")
        seen_at_swap: list[str] = []
        real_replace = os.replace

        def _watch(src: str, dst: str) -> None:
            seen_at_swap.append(Path(dst).read_text(encoding="utf-8"))
            real_replace(src, dst)

        monkeypatch.setattr(mod.os, "replace", _watch)

        mod.store_located_secret("stackowl-q29", "NEW-RECORD")

        assert seen_at_swap == ["OLD-RECORD"], (
            "the record was truncated in place before the new one was ready"
        )
        assert mod.read_located_secret("stackowl-q29") == "NEW-RECORD"
        leftovers = [p.name for p in self._file("stackowl-q29").parent.iterdir()
                     if p.name.endswith(".tmp")]
        assert leftovers == [], f"temporary secret files were left behind: {leftovers}"

    def test_a_failed_keyring_delete_KEEPS_the_locator(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import (
            SecretStoreUnreadable,
            delete_secret,
            read_located_secret,
            store_located_secret,
        )

        vault: dict[str, str] = {}
        self._keyring(
            monkeypatch,
            get_password=lambda s, _u: vault.get(s),
            set_password=lambda s, _u, v: vault.__setitem__(s, v),
            delete_password=self._locked,
        )
        store_located_secret("stackowl-q29", "RECORD")

        with pytest.raises(SecretStoreUnreadable):
            delete_secret("stackowl-q29")

        assert read_located_secret("stackowl-q29") == "RECORD", (
            "the locator went before the record, so the record is no longer findable"
        )

    def test_a_delete_ALWAYS_asks_the_keyring_even_when_the_file_holds_the_record(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import delete_secret, store_located_secret

        vault = {"stackowl-q29": "AN-OLDER-KEYRING-RECORD"}
        self._keyring(
            monkeypatch,
            get_password=lambda s, _u: vault.get(s),
            set_password=self._locked,
            delete_password=lambda s, _u: vault.pop(s),
        )
        store_located_secret("stackowl-q29", "RECORD")
        assert self._file("stackowl-q29").read_text(encoding="utf-8") == "RECORD"

        delete_secret("stackowl-q29")

        assert vault == {}, "an older keyring record outlived the delete"
        assert not self._file("stackowl-q29").exists()

    def test_a_file_that_is_not_UTF8_is_UNREADABLE_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config.secret_writer import (
            SecretStoreUnreadable,
            delete_secret,
            read_located_secret,
        )

        self._keyring(monkeypatch)
        self._file("stackowl-q29").parent.mkdir(parents=True, exist_ok=True)
        self._file("stackowl-q29").write_bytes(b"\xff\xfe\x00not text")

        with pytest.raises(SecretStoreUnreadable):
            read_located_secret("stackowl-q29")
        with pytest.raises(SecretStoreUnreadable):
            delete_secret("stackowl-q29")

    def test_a_locator_that_cannot_be_written_PUTS_THE_KEYRING_BACK(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.config import secret_writer as mod

        vault: dict[str, str] = {}
        self._keyring(
            monkeypatch,
            get_password=lambda s, _u: vault.get(s),
            set_password=lambda s, _u, v: vault.__setitem__(s, v),
            delete_password=lambda s, _u: vault.pop(s),
        )
        mod.store_located_secret("stackowl-q29", "FIRST")
        real = mod.write_atomically

        def _locator_fails(target: Path, text: str, **kwargs: int) -> None:
            if text.startswith("keychain:"):
                raise OSError("no space left on device")
            real(target, text, **kwargs)

        monkeypatch.setattr(mod, "write_atomically", _locator_fails)

        with pytest.raises(OSError):
            mod.store_located_secret("stackowl-q29", "SECOND")

        assert vault == {"stackowl-q29": "FIRST"}, "the new record is live behind the old locator"
        assert mod.read_located_secret("stackowl-q29") == "FIRST"

    def test_a_BUSY_target_is_retried_not_failed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import os

        from stackowl.config import secret_writer as mod

        self._keyring(monkeypatch)
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        real_replace = os.replace
        attempts = {"n": 0}

        def _busy(src: str, dst: str) -> None:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise PermissionError("held open by a reader")
            real_replace(src, dst)

        monkeypatch.setattr(mod.os, "replace", _busy)

        mod.store_located_secret("stackowl-q29", "RECORD")

        assert attempts["n"] == 3
        assert mod.read_located_secret("stackowl-q29") == "RECORD"
