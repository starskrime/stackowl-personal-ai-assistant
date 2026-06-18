"""ProviderProbe — connectivity checks for all enabled providers at startup."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from stackowl.config.provider import ProviderConfig
from stackowl.config.secret_resolver import SecretResolver
from stackowl.exceptions import ConfigurationError, StartupError

log = logging.getLogger("stackowl.startup")

_TIMEOUT = 10.0

# F151 — bounded retry before declaring all providers unreachable, so a transient
# network blip at boot (router reboot, DNS flap) degrades-and-retries rather than
# hard-aborting an always-on assistant. Backoff is multiplicative on the attempt.
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE_S = 1.0


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
        # Send the key via the documented x-goog-api-key header — NEVER in the URL
        # query string. A URL-embedded key leaks into httpx logs, proxy/access
        # logs, and any exception that stringifies the request URL (F150).
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        if api_key:
            headers["x-goog-api-key"] = api_key
    else:
        base = (provider.base_url or "https://api.openai.com").rstrip("/")
        url = f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    return url, headers


class ProviderProbe:
    """Runs connectivity probes for all enabled providers."""

    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base_s: float = _DEFAULT_BACKOFF_BASE_S,
    ) -> None:
        self._providers = [p for p in providers if p.enabled]
        self._max_retries = max(1, max_retries)
        self._backoff_base_s = max(0.0, backoff_base_s)

    async def check(self) -> list[ProviderResult]:
        """Probe all enabled providers, retrying a transient all-down result.

        Returns as soon as at least one provider answers ``ok``. If EVERY enabled
        provider is degraded, retries up to ``max_retries`` times with bounded
        backoff before raising a typed :class:`StartupError` carrying each
        provider's reason (F151) — so a recoverable boot-time blip survives and a
        genuine outage fails with diagnosable per-provider detail."""
        import asyncio

        log.info(
            "[startup] provider_probe.check: probing %d providers (max_retries=%d)",
            len(self._providers),
            self._max_retries,
        )
        result_list: list[ProviderResult] = []
        for attempt in range(1, self._max_retries + 1):
            results = await asyncio.gather(*[probe_provider(p) for p in self._providers])
            result_list = list(results)

            if not result_list or any(r.status == "ok" for r in result_list):
                ok = sum(1 for r in result_list if r.status == "ok")
                log.info(
                    "[startup] provider_probe.check: exit — ok=%d degraded=%d (attempt %d)",
                    ok,
                    len(result_list) - ok,
                    attempt,
                )
                return result_list

            # All degraded on this attempt — retry unless this was the last.
            if attempt < self._max_retries:
                delay = self._backoff_base_s * attempt
                log.warning(
                    "[startup] provider_probe.check: all %d providers down on attempt %d/%d — "
                    "retrying after %.1fs",
                    len(result_list),
                    attempt,
                    self._max_retries,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        # Exhausted retries with every provider degraded — fail with a typed,
        # per-provider-diagnosable StartupError (was a bare RuntimeError).
        reasons = ", ".join(
            f"{r.name} [{r.protocol}]: {r.reason or 'unreachable'}" for r in result_list
        )
        log.error(
            "[startup] provider_probe.check: all providers unreachable after %d attempts — %s",
            self._max_retries,
            reasons,
        )
        raise StartupError(4, "providers", f"No providers reachable — {reasons}")
