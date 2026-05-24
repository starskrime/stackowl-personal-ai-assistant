"""SecretResolver — dispatches keychain:, file:, and env-var secret references."""

from __future__ import annotations

import logging
import os
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
        log.debug("secret_resolver: resolved file:%s → ***", raw_path)
        return secret

    @staticmethod
    def _from_env(name: str) -> str:
        value = os.environ.get(name)
        if value is None:
            raise ConfigurationError(f"Environment variable {name!r} is not set")
        log.debug("secret_resolver: resolved env var %s → ***", name)
        return value
