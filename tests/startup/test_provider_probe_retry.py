"""OPS-4 (F151) — bounded retry + typed StartupError on all-providers-down boot.

ProviderProbe.check used to raise a bare RuntimeError("No providers reachable")
with no per-provider detail and no retry, so a transient network blip at boot
hard-aborted an always-on assistant. Now it retries with bounded backoff before
declaring all-unreachable, and raises a typed StartupError carrying each
provider's reason.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import StartupError
from stackowl.startup.provider_probe import ProviderProbe, ProviderResult


def _provider(name: str):
    from stackowl.config.provider import ProviderConfig

    return ProviderConfig(
        name=name,
        protocol="openai",
        base_url="http://localhost:1",
        enabled=True,
        default_model="test-model",
        tier="standard",
    )


@pytest.mark.asyncio
async def test_boot_survives_recoverable_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that is down on attempt 1 but up on attempt 2 must not abort boot."""
    providers = [_provider("p1")]
    probe = ProviderProbe(providers, max_retries=3, backoff_base_s=0.0)

    calls = {"n": 0}

    async def _fake_probe(provider):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return ProviderResult(
                name=provider.name, protocol="openai", status="degraded",
                latency_ms=1.0, reason="connection refused",
            )
        return ProviderResult(
            name=provider.name, protocol="openai", status="ok", latency_ms=1.0, reason=None,
        )

    monkeypatch.setattr("stackowl.startup.provider_probe.probe_provider", _fake_probe)

    results = await probe.check()
    assert any(r.status == "ok" for r in results)
    assert calls["n"] >= 2, "must have retried after the first all-down attempt"


@pytest.mark.asyncio
async def test_all_down_raises_typed_startup_error_with_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every retry leaves all providers down, raise a typed StartupError
    whose message carries each provider's reason."""
    providers = [_provider("alpha"), _provider("beta")]
    probe = ProviderProbe(providers, max_retries=2, backoff_base_s=0.0)

    async def _all_down(provider):  # type: ignore[no-untyped-def]
        return ProviderResult(
            name=provider.name, protocol="openai", status="degraded",
            latency_ms=1.0, reason="connection refused",
        )

    monkeypatch.setattr("stackowl.startup.provider_probe.probe_provider", _all_down)

    with pytest.raises(StartupError) as exc_info:
        await probe.check()

    msg = str(exc_info.value)
    assert "alpha" in msg
    assert "beta" in msg
    assert "connection refused" in msg
    assert exc_info.value.name == "providers"
