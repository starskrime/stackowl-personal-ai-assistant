"""The retired `control_plane.password` key (Q29, L2).

The dashboard password stopped being a setting: it is set on the dashboard with a
one-time setup code and kept only as a salted hash. A stackowl.yaml written before
that still carries the key, and `ControlPlaneSettings` forbids unknown keys — so
the upgrade must boot on the operator's own file, keep a password the operator
chose, and drop one anybody can look up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stackowl.config.control_plane_password_migration import (
    migrate_legacy_control_plane_password,
)

_LEGACY = """# my stackowl config
control_plane:
  # the dashboard
  enabled: true
  username: bakir
  password: {value}
webhook:
  port: 8766
"""


@pytest.fixture(autouse=True)
def _no_os_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """The import writes a hash; it must land in the per-test home, never a real keyring."""
    import keyring
    import keyring.errors

    def _absent(*_a: Any, **_k: Any) -> None:
        raise keyring.errors.NoKeyringError("no OS keyring in this test")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, _absent)


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    """Warnings are once per process; each test starts as a fresh process would."""
    from stackowl.config import control_plane_password_migration as migration_mod

    migration_mod._WARNED.clear()  # noqa: SLF001


def _write(value: str) -> Path:
    from stackowl.paths import StackowlHome

    path = StackowlHome.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LEGACY.format(value=value), encoding="utf-8")
    return path


def _lookup() -> Any:
    from stackowl.control_plane.password import ControlPlanePassword

    return ControlPlanePassword().lookup()


class TestACustomLegacyPassword:
    @pytest.mark.tripwire
    def test_it_is_imported_the_key_is_removed_and_the_platform_boots(self) -> None:
        from stackowl.config.settings import Settings

        path = _write("my-own-long-password")

        settings = Settings()

        assert settings.control_plane.username == "bakir"
        text = path.read_text(encoding="utf-8")
        assert "password" not in text, "the retired key is still in the file"
        assert "# my stackowl config" in text and "# the dashboard" in text, (
            "the rewrite lost the operator's comments"
        )
        state = _lookup()
        assert state.state == "ready"
        assert state.matches("my-own-long-password"), "the operator's password stopped working"

    @pytest.mark.tripwire
    def test_a_SHORT_one_is_imported_with_a_warning(
        self, capture_logs: list[dict[str, Any]],
    ) -> None:
        path = _write("short1")

        assert migrate_legacy_control_plane_password(path) is True

        assert any(
            r.get("level") == "WARNING" and "shorter than 12" in str(r.get("msg"))
            for r in capture_logs
        )
        assert _lookup().matches("short1")

    @pytest.mark.tripwire
    def test_it_is_IDEMPOTENT(self) -> None:
        path = _write("my-own-long-password")

        assert migrate_legacy_control_plane_password(path) is True
        assert migrate_legacy_control_plane_password(path) is False

    @pytest.mark.tripwire
    def test_a_password_already_stored_is_NOT_overwritten(self) -> None:
        from stackowl.control_plane.password import ControlPlanePassword

        ControlPlanePassword().set("the-dashboard-one-wins")
        path = _write("my-own-long-password")

        assert migrate_legacy_control_plane_password(path) is True

        state = _lookup()
        assert state.matches("the-dashboard-one-wins")
        assert not state.matches("my-own-long-password")
        assert "password" not in path.read_text(encoding="utf-8")

    @pytest.mark.tripwire
    def test_the_value_never_reaches_a_log(self, capture_logs: list[dict[str, Any]]) -> None:
        migrate_legacy_control_plane_password(_write("my-own-long-password"))

        assert capture_logs, "nothing was logged — this guard has gone blind"
        assert "my-own-long-password" not in json.dumps(capture_logs, default=str)


class TestAPublishedLegacyPassword:
    @pytest.mark.tripwire
    @pytest.mark.parametrize("value", [
        "admin", "keychain:stackowl-control-plane-password", "file:/etc/stackowl/pw",
    ])
    def test_it_is_DROPPED_and_the_install_is_in_setup_mode(self, value: str) -> None:
        path = _write(value)

        assert migrate_legacy_control_plane_password(path) is True

        assert "password" not in path.read_text(encoding="utf-8")
        assert _lookup().state == "setup", "a published password was imported as the owner's"


class TestItNeverBlocksABoot:
    @pytest.mark.tripwire
    def test_an_unreadable_store_KEEPS_the_key_for_the_next_start_and_still_boots(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.config.secret_writer import SecretStoreUnreadable
        from stackowl.config.settings import Settings
        from stackowl.control_plane import password as password_mod

        real = password_mod.read_located_secret

        def _unreadable(_service: str) -> str | None:
            raise SecretStoreUnreadable("the keyring is locked")

        monkeypatch.setattr(password_mod, "read_located_secret", _unreadable)
        path = _write("my-own-long-password")

        assert Settings().control_plane.username == "bakir"
        assert "password" in path.read_text(encoding="utf-8"), (
            "the only copy of the operator's password was removed before it was imported"
        )
        assert any(
            r.get("level") == "WARNING" and "not imported yet" in str(r.get("msg"))
            for r in capture_logs
        )

        monkeypatch.setattr(password_mod, "read_located_secret", real)
        Settings()
        assert "password" not in path.read_text(encoding="utf-8")
        assert _lookup().matches("my-own-long-password")

    @pytest.mark.tripwire
    def test_a_file_that_cannot_be_rewritten_is_IGNORED_with_a_remedy(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.config import control_plane_password_migration as migration_mod
        from stackowl.config.settings import Settings

        def _disk_full(*_a: Any, **_k: Any) -> None:
            raise OSError("no space left on device")

        path = _write("admin")
        monkeypatch.setattr(migration_mod, "write_atomically", _disk_full)
        settings = Settings()

        assert settings.control_plane.username == "bakir"
        assert "password" in path.read_text(encoding="utf-8")
        assert any(
            r.get("level") == "WARNING"
            and "control_plane_password_migration" in str(r.get("msg"))
            and "remedy" in (r.get("fields") or {})
            for r in capture_logs
        ), "the key was left in the file with no remedy said"

    @pytest.mark.tripwire
    def test_an_unremovable_key_WARNS_once_and_the_file_is_never_truncated(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.config import control_plane_password_migration as migration_mod
        from stackowl.config.settings import Settings

        def _disk_full(*_a: Any, **_k: Any) -> None:
            raise OSError("no space left on device")

        path = _write("admin")
        before = path.read_text(encoding="utf-8")
        monkeypatch.setattr(migration_mod, "write_atomically", _disk_full)

        for _ in range(3):
            Settings()

        assert path.read_text(encoding="utf-8") == before, "a failed rewrite changed the file"
        warned = [r for r in capture_logs if r.get("level") == "WARNING"
                  and "control_plane_password_migration" in str(r.get("msg"))]
        assert len(warned) == 2, [r.get("msg") for r in warned]

    @pytest.mark.tripwire
    def test_a_legacy_ENVIRONMENT_password_is_ignored_not_a_refused_boot(
        self, monkeypatch: pytest.MonkeyPatch, capture_logs: list[dict[str, Any]],
    ) -> None:
        from stackowl.config.settings import Settings

        monkeypatch.setenv("STACKOWL_CONTROL_PLANE__PASSWORD", "admin")

        settings = Settings()

        assert settings.control_plane.enabled is True
        assert any(
            r.get("level") == "WARNING"
            and "retired control_plane.password" in str(r.get("msg"))
            and (r.get("fields") or {}).get("source") == "environment"
            for r in capture_logs
        ), "the environment's retired key was not reported"
