"""PROV-1 (F150) — the Gemini provider probe must never embed the API key in a URL.

The key has to travel in the ``x-goog-api-key`` header, and no probe failure
``reason`` (which is surfaced/logged) may contain the secret.
"""

from __future__ import annotations

import httpx
import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.startup.provider_probe import _build_request, probe_provider

_SECRET = "AIzaSyTESTSECRETKEY1234567890abcdef"


def test_gemini_request_carries_key_in_header_not_url() -> None:
    cfg = ProviderConfig(name="g", protocol="gemini", api_key=_SECRET, default_model="gemini-1.5-flash", tier="fast")
    url, headers = _build_request(cfg, _SECRET)
    assert _SECRET not in url, "API key must not appear in the probe URL"
    assert headers.get("x-goog-api-key") == _SECRET


@pytest.mark.asyncio
async def test_gemini_probe_failure_reason_never_leaks_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_TEST_KEY", _SECRET)
    cfg = ProviderConfig(
        name="g", protocol="gemini", api_key="GEMINI_TEST_KEY",
        default_model="gemini-1.5-flash", tier="fast",
    )

    async def _boom(self, url, headers=None):  # noqa: ANN001
        # A real httpx error embeds the request URL in its string form; verify the
        # URL we send carries no key, so even a leaked-URL error stays clean.
        assert _SECRET not in str(url)
        raise httpx.ConnectError(f"cannot connect to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom)
    result = await probe_provider(cfg)
    assert result.status == "degraded"
    assert result.reason is not None
    assert _SECRET not in result.reason
