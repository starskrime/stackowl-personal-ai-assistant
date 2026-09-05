"""SecretResolver — dispatches keychain:, file:, and env-var secret references."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from stackowl.exceptions import ConfigurationError

log = logging.getLogger("stackowl.config")


class SecretResolver:
    """Resolves secret references without writing the raw value to any log.

    Supported formats:
    - ``keychain:<service>``  → OS keychain (keyring library)
    - ``file:<absolute-path>`` → read file, strip whitespace
    - ``<NAME>``               → ``os.environ["NAME"]``
    """

    @staticmethod
    def resolve(value: str) -> str:
        if value.startswith("keychain:"):
            return SecretResolver._from_keychain(value[len("keychain:") :])
        if value.startswith("file:"):
            return SecretResolver._from_file(value[len("file:") :])
        return SecretResolver._from_env(value)

    @staticmethod
    def _from_keychain(service: str) -> str:
        try:
            import keyring  # local import — optional dependency

            secret: str | None = keyring.get_password(service, service)
        except Exception as exc:
            raise ConfigurationError(f"keychain:{service} — keyring lookup failed: {exc}") from exc
        if secret is None:
            raise ConfigurationError(f"keychain:{service} — not found in OS keychain")
        log.debug("secret_resolver: resolved keychain:%s → ***", service)
        return secret

    @staticmethod
    def _from_file(raw_path: str) -> str:
        path = Path(raw_path)
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigurationError(f"file:{raw_path} — could not read secret file: {exc}") from exc
        SecretResolver._warn_if_widely_readable(path)
        log.debug("secret_resolver: resolved file:%s → ***", raw_path)
        return secret

    @staticmethod
    def _warn_if_widely_readable(path: Path) -> None:
        """Say so when a secret file can be read by anyone on the box.

        This read whatever was there and never looked at the mode. Measured
        2026-09-05 the operator's own secrets are correct — `700` on the directory,
        `0600` on every key — so this is SILENT on a healthy box, which is the point:
        a guard that also fires on the correct case is one its reader learns to
        ignore. The failure it exists for is the invisible one: a restore, a `cp`, an
        editor writing a fresh file, or an archive extracted without modes, leaving a
        key world-readable. The platform would read it, work perfectly, and never
        mention it.

        IT WARNS AND STILL RETURNS THE SECRET. A world-readable key is already
        exposed to every process on the machine; refusing to start does not un-expose
        it, and it takes the platform down to report something stopping cannot fix.
        That is D18.9's rule — fail closed when refusing PREVENTS the harm, warn when
        the harm has already happened.

        POSIX ONLY, deliberately. Mode bits do not carry this meaning on Windows, and
        `scripts/boundaries/b4.py` (gated since D18.7) exists to stop that assumption
        being made silently.
        """
        if sys.platform == "win32":
            return
        try:
            mode = os.stat(path).st_mode & 0o777
        except Exception as exc:  # noqa: BLE001 — an unmeasurable mode is not an exposure
            # "I could not check" and "it is exposed" are different claims, and this
            # repo has paid for reporting the first as the second.
            log.debug("secret_resolver: could not stat %s: %s", path, exc)
            return
        if mode & 0o077:
            log.warning(
                "secret_resolver: %s is mode %o — readable beyond its owner, so every "
                "process on this machine can read this credential. chmod 600 it.",
                path, mode,
            )

    @staticmethod
    def _from_env(name: str) -> str:
        value = os.environ.get(name)
        if value is None:
            raise ConfigurationError(f"Environment variable {name!r} is not set")
        log.debug("secret_resolver: resolved env var %s → ***", name)
        return value
