"""On-disk stackowl.yaml migration: the retired `control_plane.password` key (Q29).

The dashboard password stopped being a setting: it is set on the dashboard with a
one-time setup code and kept only as a salted hash in the secret store (L2). A
stackowl.yaml written before that still carries the key, and
`ControlPlaneSettings` forbids unknown keys — so without this, an upgrade would
refuse to boot on the operator's own file.

Wired into ``_YamlSource._load()`` beside the provider-tier migration, the one
choke point every ``Settings()`` construction goes through. Comment-preserving
(ruamel), idempotent, and it never blocks a boot:

* a CUSTOM value (not the shipped ``admin``, not a ``keychain:``/``file:``
  reference) is imported ONCE as the hash when none is stored — the operator's
  chosen password keeps working — with a WARNING when it is shorter than the
  12-character floor the dashboard now enforces;
* ``admin`` and reference values are published, so they are dropped: the install
  starts in setup mode;
* the key is then removed from the file — written to a sibling temp file and
  swapped in, so a crash mid-write cannot truncate the operator's config. If the
  file cannot be written, or the store cannot take the import, the key stays on
  disk, is IGNORED at parse (:func:`drop_legacy_control_plane_password`) and a
  WARNING gives the remedy — once per process, not on every ``Settings()``.
"""

from __future__ import annotations

import io
import stat
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from stackowl.config.secret_writer import write_atomically
from stackowl.infra.observability import log

_SHIPPED_DEFAULT = "admin"
_REFERENCE_PREFIXES = ("keychain:", "file:")

#: What this process has already warned about. `Settings()` is built many times
#: a run, and a WARNING repeated on every construction buries the first one.
_WARNED: set[tuple[str, str]] = set()


def _warn_once(kind: str, subject: str, message: str, details: dict[str, Any]) -> None:
    """Loud the first time *kind* is seen for *subject*, quiet on every repeat."""
    first = (kind, subject) not in _WARNED
    _WARNED.add((kind, subject))
    (log.config.warning if first else log.config.debug)(
        message, extra={"_fields": {**details, "repeat": not first}}
    )


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def _importable(value: Any) -> str | None:
    """The legacy value as a password worth keeping, or ``None`` when it is published."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value)
    if not text or text == _SHIPPED_DEFAULT or text.startswith(_REFERENCE_PREFIXES):
        return None
    return text


def migrate_legacy_control_plane_password(path: Path) -> bool:
    """Import a custom legacy password once and remove the key. True iff the file was rewritten.

    Never raises.
    """
    # 1. ENTRY
    log.config.debug(
        "[config] control_plane_password_migration.migrate: entry",
        extra={"_fields": {"path": str(path)}},
    )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError) as exc:
        log.config.warning(
            "[config] control_plane_password_migration.migrate: exit — could not read "
            "the config file",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return False
    # A cheap gate: every Settings() construction passes through here, and a
    # second ruamel parse of a file without the word is pure cost.
    if "password" not in text:
        return False

    yml = _yaml()
    try:
        data: Any = yml.load(text)
    except Exception as exc:  # noqa: BLE001 — the real parse reports it loudly
        log.config.warning(
            "[config] control_plane_password_migration.migrate: exit — parse failed, "
            "leaving untouched",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return False
    section = data.get("control_plane") if isinstance(data, dict) else None
    if not isinstance(section, dict) or "password" not in section:
        return False

    # 2. DECISION — import a custom value, drop a published one.
    legacy = _importable(section["password"])
    if legacy is not None and not _import(legacy, path):
        return False

    # 3. STEP — remove the key, swapping the rewritten file in whole.
    del section["password"]
    buffer = io.StringIO()
    try:
        yml.dump(data, buffer)
        write_atomically(path, buffer.getvalue(), mode=stat.S_IMODE(path.stat().st_mode))
    except Exception as exc:  # noqa: BLE001 — never block a boot
        _warn_once(
            "write-failed", str(path),
            "[config] control_plane_password_migration.migrate: exit — could not remove "
            "the retired key; it is ignored",
            {
                "path": str(path),
                "error": str(exc),
                "remedy": f"delete `control_plane.password` from {path} by hand — the "
                          "password is no longer a setting",
            },
        )
        return False

    # 4. EXIT
    for stale in [key for key in _WARNED if key[1] == str(path)]:
        _WARNED.discard(stale)
    imported = legacy is not None
    log.config.info(
        "[config] control_plane_password_migration.migrate: exit — removed the retired "
        "control_plane.password key",
        extra={"_fields": {"path": str(path), "imported": imported}},
    )
    return True


def _import(legacy: str, path: Path) -> bool:
    """Store *legacy* as the hash unless one is stored. False when the key must stay."""
    from stackowl.control_plane.password import (  # noqa: PLC0415 — config must not import the control plane at module load
        MIN_LENGTH,
        ControlPlanePassword,
        PasswordStoreUnavailable,
    )

    owner = ControlPlanePassword()
    state = owner.lookup()
    if state.state == "unavailable":
        _warn_once(
            "not-imported", str(path),
            "[config] control_plane_password_migration.import: the secret store cannot "
            "be read, so the legacy password is not imported yet; the key stays in the "
            "file, ignored, and the next start tries again",
            {"path": str(path), "remedy": state.remedy},
        )
        return False
    if state.state == "ready":
        log.config.info(
            "[config] control_plane_password_migration.import: a dashboard password is "
            "already stored — the legacy YAML value is not imported",
        )
        return True
    if len(legacy) < MIN_LENGTH:
        log.config.warning(
            "[config] control_plane_password_migration.import: the imported dashboard "
            f"password is shorter than {MIN_LENGTH} characters",
            extra={"_fields": {
                "remedy": "sign in and change it on the dashboard — new passwords need "
                          f"at least {MIN_LENGTH} characters",
            }},
        )
    try:
        owner.set(legacy)
    except PasswordStoreUnavailable as exc:
        _warn_once(
            "not-imported", str(path),
            "[config] control_plane_password_migration.import: the legacy password could "
            "not be stored; the key stays in the file, ignored, and the next start tries "
            "again",
            {"path": str(path), "error": str(exc), "remedy": exc.remedy},
        )
        return False
    log.config.warning(
        "[config] control_plane_password_migration.import: imported the custom "
        "control_plane.password as the dashboard password hash",
        extra={"_fields": {"path": str(path)}},
    )
    return True


def drop_legacy_control_plane_password(
    data: dict[str, Any],
    *,
    source: str = "config file",
    remedy: str = "delete `control_plane.password` from stackowl.yaml — the dashboard "
                  "password is set on the dashboard with a setup code",
) -> None:
    """Ignore a retired `control_plane.password` a source still carries."""
    section = data.get("control_plane")
    if isinstance(section, dict) and "password" in section:
        del section["password"]
        if not section:
            del data["control_plane"]
        _warn_once(
            "ignored", source,
            "[config] control_plane_password_migration: ignoring the retired "
            "control_plane.password key",
            {"source": source, "remedy": remedy},
        )
