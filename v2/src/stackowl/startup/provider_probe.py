"""ProviderProbe — connectivity checks for all enabled providers at startup."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from stackowl.config.provider import ProviderConfig
from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError

log = logging.getLogger("stackowl.startup")

_TIMEOUT = 10.0


@dataclass(frozen=True)
class ProviderResult:
    name: str
    protocol: str
    status: Literal["ok", "degraded"]
    latency_ms: float
    reason: str | None


async def probe_provider(provider: ProviderConfig) -> ProviderResult:
    """Attempt a lightweight connectivity check for a single provider."""
    log.debug("[startup] provider_probe: entry name=%s protocol=%s", provider.name, provider.protocol)
    t0 = time.monotonic()

    api_key: str | None = None
    if provider.api_key is not None:
        try:
            api_key = SecretResolver.resolve(provider.api_key)
        except ConfigurationError as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            log.warning(
                "[startup] provider %s [%s]: degraded — secret unavailable: %s",
                provider.name,
                provider.protocol,
                exc,
            )
            return ProviderResult(
                name=provider.name,
                protocol=provider.protocol,
                status="degraded",
                latency_ms=latency_ms,
                reason=f"secret unavailable: {exc}",
            )

    url, headers = _build_request(provider, api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        latency_ms = (time.monotonic() - t0) * 1000
        if resp.status_code >= 500:  # noqa: PLR2004
            reason = f"HTTP {resp.status_code}"
            log.warning("[startup] provider %s [%s]: degraded — %s", provider.name, provider.protocol, reason)
            return ProviderResult(
                name=provider.name,
                protocol=provider.protocol,
                status="degraded",
                latency_ms=latency_ms,
                reason=reason,
            )
        log.info(
            "[startup] provider %s [%s]: ok (%.0fms)",
            provider.name,
            provider.protocol,
            latency_ms,
        )
        return ProviderResult(
            name=provider.name,
            protocol=provider.protocol,
            status="ok",
            latency_ms=latency_ms,
            reason=None,
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        reason = str(exc)
        log.warning("[startup] provider %s [%s]: unreachable — %s", provider.name, provider.protocol, reason)
        return ProviderResult(
            name=provider.name,
            protocol=provider.protocol,
            status="degraded",
            latency_ms=latency_ms,
            reason=reason,
        )


def _build_request(provider: ProviderConfig, api_key: str | None) -> tuple[str, dict[str, str]]:
    headers: dict[str, str] = {}
    if provider.protocol == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider.protocol == "gemini":
        base = "https://generativelanguage.googleapis.com/v1beta/models"
        url = f"{base}?key={api_key}" if api_key else base
    else:
        base = (provider.base_url or "https://api.openai.com").rstrip("/")
        url = f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    return url, headers


class ProviderProbe:
    """Runs connectivity probes for all enabled providers."""

    def __init__(self, providers: list[ProviderConfig]) -> None:
        self._providers = [p for p in providers if p.enabled]

    async def check(self) -> list[ProviderResult]:
        """Probe all enabled providers. Raises if ALL are unreachable."""
        import asyncio

        log.info("[startup] provider_probe.check: probing %d providers", len(self._providers))
        results = await asyncio.gather(*[probe_provider(p) for p in self._providers])
        result_list = list(results)

        if result_list and all(r.status == "degraded" for r in result_list):
            raise RuntimeError("No providers reachable")

        ok = sum(1 for r in result_list if r.status == "ok")
        log.info("[startup] provider_probe.check: exit — ok=%d degraded=%d", ok, len(result_list) - ok)
        return result_list
