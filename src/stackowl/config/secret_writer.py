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

from stackowl.infra.observability import log
from stackowl.paths import StackowlHome


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
    # exists on disk at a wider mode. chmod after as well so a pre-existing
    # file written under an earlier umask is also tightened.
    secrets_dir = StackowlHome.secrets_dir()
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secret_file = secrets_dir / f"{service_name}.key"
    fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(secret)
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
