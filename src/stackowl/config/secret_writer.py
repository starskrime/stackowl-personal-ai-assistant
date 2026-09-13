"""Shared secret-writer — persist a secret and return a SecretResolver ref.

Single source of truth for storing provider/channel API keys. Tries the OS
keyring first; on any failure falls back to a mode-0600 file under
``StackowlHome.secrets_dir()``. The returned ``yaml_ref`` is understood by
:class:`stackowl.config.secret_resolver.SecretResolver`:

- ``keychain:{service_name}`` → OS keyring (``get_password(service, service)``)
- ``file:{absolute_path}``    → read file, strip whitespace

The raw secret is NEVER logged or echoed — only the service name and the
storage location.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome

#: How many times the swap is tried when the target is busy. Windows raises
#: PermissionError from `os.replace` while another process holds the file open for
#: reading — which, for a record read on every dashboard request, is routine.
_REPLACE_ATTEMPTS = 5


def write_atomically(target: Path, text: str, *, mode: int = 0o600) -> None:
    """Write *text* to *target* at *mode*, REPLACING it in one durable step.

    At *mode* from the first byte (no wider window), and written to a sibling temp
    file that `os.replace` swaps in. An in-place `O_TRUNC` write leaves an EMPTY
    file visible to a concurrent reader for as long as the write takes, and a
    per-request lookup of the dashboard password (Q29) reads an empty record as a
    damaged one. The temp file is fsynced before the swap and the directory after
    it where the platform allows, so a crash cannot leave the rename pointing at
    unwritten bytes. `os.replace` is atomic on POSIX and Windows alike.
    """
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if hasattr(os, "fchmod"):
                # The umask narrows O_CREAT's mode; the caller asked for exactly this.
                os.fchmod(fh.fileno(), mode)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(1, _REPLACE_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS:
                    raise
                log.config.info(
                    "[config] write_atomically: the target is busy — retrying the swap",
                    extra={"_fields": {"file": target.name, "attempt": attempt}},
                )
                time.sleep(0.05 * attempt)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_exc:
            log.config.warning(
                "[config] write_atomically: could not remove a temporary file",
                extra={"_fields": {"file": tmp.name, "reason": str(cleanup_exc)}},
            )
        raise
    _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename durable where a directory can be synced (not on Windows)."""
    if os.name == "nt":
        return
    try:
        dfd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        log.config.debug(
            "[config] write_atomically: the directory could not be opened to sync",
            extra={"_fields": {"reason": str(exc)}},
        )
        return
    try:
        os.fsync(dfd)
    except OSError as exc:
        log.config.debug(
            "[config] write_atomically: the directory could not be synced",
            extra={"_fields": {"reason": str(exc)}},
        )
    finally:
        os.close(dfd)


def store_secret(service_name: str, secret: str) -> tuple[str, str]:
    """Persist *secret* under *service_name* and return ``(description, yaml_ref)``.

    ``description`` is a human-readable storage location (e.g. ``"OS keyring"``
    or ``"file:/abs/path"``); ``yaml_ref`` is the SecretResolver-compatible
    reference to write into ``stackowl.yaml``.
    """
    # 1. ENTRY — never log the secret itself
    log.config.debug(
        "[config] store_secret: entry",
        extra={"_fields": {"service": service_name}},
    )

    # 2. DECISION — try OS keyring first
    try:
        import keyring

        keyring.set_password(service_name, service_name, secret)
        log.config.debug(
            "[config] store_secret: exit — stored in OS keyring",
            extra={"_fields": {"service": service_name}},
        )
        return "OS keyring", f"keychain:{service_name}"
    except Exception as exc:  # noqa: BLE001 — keyring optional; fall back loudly
        log.config.debug(
            "[config] store_secret: keyring unavailable — falling back to file",
            extra={"_fields": {"service": service_name, "reason": str(exc)}},
        )

    # 3. STEP — fall back to a mode-0600 file under ~/.stackowl/.secrets/.
    # Create it 0600 atomically (no world-readable window) — the secret never
    # exists on disk at a wider mode — and swap it in whole, so no reader ever
    # sees it half-written. chmod after as well so a pre-existing file written
    # under an earlier umask is also tightened.
    secrets_dir = StackowlHome.secrets_dir()
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secret_file = secrets_dir / f"{service_name}.key"
    write_atomically(secret_file, secret)
    try:
        secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        log.config.warning(
            "[config] store_secret: could not set file permissions",
            extra={"_fields": {"service": service_name, "reason": str(exc)}},
        )

    # 4. EXIT
    yaml_ref = f"file:{secret_file}"
    log.config.debug(
        "[config] store_secret: exit — stored in secret file",
        extra={"_fields": {"service": service_name}},
    )
    return f"file:{secret_file}", yaml_ref


# ---------------------------------------------------------------------------
# LOCATED secrets — a record whose ABSENCE can be told apart from an unreadable
# store.
#
# `store_secret` writes to the OS keyring when it can and to the 0600 file when it
# cannot, and does not tell a later reader which. For a token that is fine — a
# reader asks both and takes what answers. It is NOT fine for a fact whose absence
# MEANS something: "no dashboard password is stored" puts the install in setup
# mode, and a keyring that is merely locked this minute must never read as that.
#
# MEASURED on this box 2026-09-12: the keyring backend is SecretService and EVERY
# call raises `KeyringLocked`. So "the keyring raised" cannot mean "unreadable"
# on its own — on a headless host it is the normal answer and the record was never
# there. The only thing that knows is the WRITE, so the write leaves a locator: the
# 0600 file holds either the record itself or exactly `keychain:<service>`. The
# file is the authority on existence, the keyring only ever holds the value.
# ---------------------------------------------------------------------------


class SecretStoreUnreadable(RuntimeError):
    """The secret store holds, or may hold, the record and cannot be read or changed.

    Never a synonym for "absent": a caller that treated it as one would read a
    locked keyring as "nothing stored".
    """


def _secret_file(service_name: str) -> Path:
    return StackowlHome.secrets_dir() / f"{service_name}.key"


def _locator(service_name: str) -> str:
    return f"keychain:{service_name}"


def _read_text(path: Path) -> str | None:
    """The file's stripped text, ``None`` when it does not exist, or raise.

    A file that is not UTF-8 is UNREADABLE, not a crash: it used to raise
    `UnicodeDecodeError` straight through a dashboard request as a 500, and out
    of `Settings()` via the config migration's lookup.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretStoreUnreadable(f"{path} exists but could not be read: {exc}") from exc


def store_located_secret(service_name: str, secret: str) -> str:
    """Persist *secret* so :func:`read_located_secret` can tell absent from unreadable.

    Returns the human-readable location. Raises whatever the store raised — the
    caller decides what a failed write means — and a keyring write whose locator
    could not be written is put back first, so a failure changes nothing.
    """
    # 1. ENTRY — never the secret itself
    log.config.debug(
        "[config] store_located_secret: entry",
        extra={"_fields": {"service": service_name}},
    )
    secret_file = _secret_file(service_name)

    # 2. DECISION — what a failed locator write would have to put back.
    try:
        was_located = _read_text(secret_file) == _locator(service_name)
    except SecretStoreUnreadable:
        was_located = False
    previous_keyring: str | None = None
    if was_located:
        try:
            import keyring

            previous_keyring = keyring.get_password(service_name, service_name)
        except Exception as exc:  # noqa: BLE001 — only the undo needs it
            log.config.info(
                "[config] store_located_secret: the current keyring record could not be "
                "read, so a failed write could not put it back",
                extra={"_fields": {"service": service_name, "reason": str(exc)[:200]}},
            )

    # 3. STEP — `store_secret` picks the backend; a keyring record gets a locator,
    # a file record IS its own locator.
    description, yaml_ref = store_secret(service_name, secret)
    if yaml_ref == _locator(service_name):
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_atomically(secret_file, yaml_ref)
        except Exception:
            _undo_keyring_write(service_name, previous_keyring)
            raise

    # 4. EXIT
    log.config.debug(
        "[config] store_located_secret: exit",
        extra={"_fields": {"service": service_name, "stored_in": description}},
    )
    return description


def _undo_keyring_write(service_name: str, previous_keyring: str | None) -> None:
    """The keyring took the new record and its locator did not land: put the keyring back.

    Otherwise the NEW value sits live behind the OLD locator while the caller
    reports that nothing changed.
    """
    try:
        import keyring

        if previous_keyring is None:
            keyring.delete_password(service_name, service_name)
        else:
            keyring.set_password(service_name, service_name, previous_keyring)
    except Exception as exc:  # noqa: BLE001 — B5, never silent
        log.config.error(
            "[config] store_located_secret: the locator write failed and the keyring "
            "could not be put back",
            exc_info=exc,
            extra={"_fields": {"service": service_name}},
        )
        return
    log.config.warning(
        "[config] store_located_secret: the locator write failed — the keyring record "
        "was put back",
        extra={"_fields": {"service": service_name}},
    )


def read_located_secret(service_name: str) -> str | None:
    """Return the record, ``None`` when nothing was ever stored, or raise.

    ``""`` means the locator points at a keyring entry that is not there — a
    DAMAGED record, which the caller reports rather than treating as absent.
    Raises :class:`SecretStoreUnreadable` when the file or the keyring the record
    lives in cannot be read.
    """
    # 1. ENTRY
    log.config.debug(
        "[config] read_located_secret: entry",
        extra={"_fields": {"service": service_name}},
    )

    # 2. DECISION — the file is the authority on whether a record exists.
    content = _read_text(_secret_file(service_name))
    if content is None:
        log.config.debug(
            "[config] read_located_secret: exit — nothing stored",
            extra={"_fields": {"service": service_name}},
        )
        return None

    if content != _locator(service_name):
        log.config.debug(
            "[config] read_located_secret: exit — record read from the secret file",
            extra={"_fields": {"service": service_name}},
        )
        return content

    # 3. STEP — the record lives in the OS keyring, so a keyring failure is real.
    try:
        import keyring

        value = keyring.get_password(service_name, service_name)
    except Exception as exc:  # noqa: BLE001 — every keyring failure is "unreadable" here
        raise SecretStoreUnreadable(
            f"the OS keyring holds {service_name} and could not be read: {exc}"
        ) from exc

    # 4. EXIT
    present = value is not None
    log.config.debug(
        "[config] read_located_secret: exit — record read from the OS keyring",
        extra={"_fields": {"service": service_name, "present": present}},
    )
    return "" if value is None else value.strip()


def delete_secret(service_name: str) -> None:
    """Remove a secret written by :func:`store_located_secret` (or a file-backed one).

    Idempotent: nothing stored is success. Raises :class:`SecretStoreUnreadable`
    when the record exists and could not be removed; the locator is removed only
    AFTER the keyring record, so a failed delete leaves the record findable.
    """
    # 1. ENTRY
    log.config.debug(
        "[config] delete_secret: entry",
        extra={"_fields": {"service": service_name}},
    )
    secret_file = _secret_file(service_name)

    # 2. DECISION — where does the record live?
    content = _read_text(secret_file)
    located = content == _locator(service_name)

    # 3. STEP — the keyring ALWAYS, then the file (record or locator). Asked even
    # when the file holds the record itself: a record the keyring took before the
    # file did, or one whose locator write failed, would otherwise outlive the delete.
    try:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(service_name, service_name)
        except keyring.errors.PasswordDeleteError:
            log.config.debug(
                "[config] delete_secret: no keyring entry to remove",
                extra={"_fields": {"service": service_name}},
            )
    except Exception as exc:  # noqa: BLE001 — fatal only where the record lives
        if located:
            raise SecretStoreUnreadable(
                f"the OS keyring holds {service_name} and it could not be removed: {exc}"
            ) from exc
        log.config.debug(
            "[config] delete_secret: the OS keyring could not be asked; no record of "
            "this service is known to live there",
            extra={"_fields": {"service": service_name, "reason": str(exc)[:200]}},
        )

    try:
        secret_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise SecretStoreUnreadable(f"could not remove {secret_file}: {exc}") from exc

    # 4. EXIT
    was_stored = content is not None
    log.config.debug(
        "[config] delete_secret: exit — removed",
        extra={"_fields": {"service": service_name, "was_stored": was_stored}},
    )
