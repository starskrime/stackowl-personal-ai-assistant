"""detect_timezone_from_ip — best-effort public-IP timezone geolocation.

Backs the ``/config detect-timezone`` command: the box has no GPS/locale
signal of its own, so the user's IANA timezone is inferred from the public
IP address of the network it is running on, via a free no-API-key
geolocation lookup. Best-effort only — never raises; a failure just means
the caller reports it could not determine a timezone and the user sets
``system.timezone`` manually instead (``/config set system.timezone <IANA
name>``).
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from stackowl.infra.observability import log

_TIMEOUT = 5.0
_URL = "https://ipapi.co/timezone/"


async def detect_timezone_from_ip() -> str | None:
    """Return the IANA timezone for this network's public IP, or ``None``."""
    log.config.debug("[config] timezone_detect.detect: entry")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_URL)
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise into a command handler
        log.config.warning(
            "[config] timezone_detect.detect: exit — request failed", exc_info=exc
        )
        return None
    if resp.status_code != 200:  # noqa: PLR2004
        log.config.warning(
            "[config] timezone_detect.detect: exit — non-200 response",
            extra={"_fields": {"status_code": resp.status_code}},
        )
        return None
    tz = resp.text.strip()
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        log.config.warning(
            "[config] timezone_detect.detect: exit — unexpected/invalid response body",
            extra={"_fields": {"body_preview": tz[:80]}},
        )
        return None
    log.config.info(
        "[config] timezone_detect.detect: exit — detected", extra={"_fields": {"tz": tz}}
    )
    return tz
