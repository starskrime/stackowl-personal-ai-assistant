"""A provider that cannot be reached is DOWN, and the platform must be able to say so.

MEASURED 2026-09-03 during a live outage. His VPN tunnel dropped, the only
enabled provider's hostname stopped resolving, and every turn failed. The health
sweep reported::

    {"down": [], "degraded": ["provider:NeraAiRaw"]}

"Degraded" — while the assistant could not answer a single message for six hours.

THE CONTRADICTION IS INSIDE ONE FUNCTION. ``provider_probe`` has three failure
branches and every one returns ``degraded``, including the branch whose own log
line calls it unreachable::

    log.warning("[startup] provider %s [%s]: unreachable — %s", ...)
    return ProviderResult(..., status="degraded", ...)

It says UNREACHABLE and reports DEGRADED, three lines apart. ``ProviderResult``
could not express anything else: its status was ``Literal["ok", "degraded"]``, so
"down" was not in the vocabulary. ``ProviderContributor`` then faithfully
collapsed it (``"ok" if result.status == "ok" else "degraded"``) — the
information was never produced, not thrown away downstream.

WHAT IT COSTS. ``HealthAggregator`` derives the whole system's verdict from the
``down`` list, and the CLI renders it. Because a provider can never be down, the
SYSTEM can never be reported down for a provider outage — only degraded — no
matter how completely it has stopped working. The sibling contributor in the same
file already gets this right ("down if any server is dead, degraded if all alive
but we saw failures"); the one dependency whose loss makes the assistant useless
is the one that cannot say it.

WHY THIS IS SAFE, CHECKED BEFORE CHANGING IT. ``aggregator.is_live()`` returns
False on any ``down`` subsystem, and ``watchdog`` stops pinging systemd when
liveness fails — so a careless change here would turn a VPN outage into a restart
loop. It does not, because the design already separated the two: the watchdog is
wired to ``_build_liveness_aggregator()``, which registers ONLY ``DbContributor``
and ``FilesystemContributor``, and the orchestrator says why in as many words —
"Network provider health is deliberately EXCLUDED: a provider outage is not a
reason to kill this process." The reporting aggregator and the liveness
aggregator are different objects, and only the reporting one sees providers.

THE RULE, stated so the next branch inherits it: can this provider serve? Cannot
be reached, or has no usable credential, means no — that is DOWN. Reachable but
returning 5xx means maybe, and may be transient — that stays DEGRADED.
"""

from __future__ import annotations

import pytest

from stackowl.startup.provider_probe import ProviderResult

pytestmark = pytest.mark.anyio


def _result(status: str, reason: str) -> ProviderResult:
    return ProviderResult(
        name="NeraAiRaw", protocol="openai", status=status,  # type: ignore[arg-type]
        latency_ms=1.0, reason=reason,
    )


def test_the_result_type_can_express_down() -> None:
    """THE ROOT CAUSE. The status Literal was ("ok", "degraded") — "down" was not
    in the vocabulary, so no branch could have reported it however hard it tried."""
    assert _result("down", "unreachable").status == "down"


async def test_an_unreachable_provider_reports_down(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The live shape: DNS failure. Its own log line already said "unreachable"."""
    import httpx

    from stackowl.startup import provider_probe

    class _Boom:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, *_a: object, **_k: object) -> object:
            raise httpx.ConnectError("[Errno -5] No address associated with hostname")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _Boom())
    result = await provider_probe.probe_provider(_cfg())
    assert result.status == "down", (
        f"a provider that cannot be reached reported {result.status!r} — the "
        "system verdict then understates a total outage as degraded"
    )


async def test_a_server_returning_5xx_stays_degraded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """THE OTHER DIRECTION, and the reason this is not just "failures are down".
    A 5xx means the provider ANSWERED — it is reachable and may recover on the
    next call. Calling that "down" would make the distinction useless again, in
    the opposite direction."""
    import httpx

    from stackowl.startup import provider_probe

    class _Resp:
        status_code = 503

    class _Client:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def get(self, *_a: object, **_k: object) -> object:
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _Client())
    result = await provider_probe.probe_provider(_cfg())
    assert result.status == "degraded"


def _cfg():  # type: ignore[no-untyped-def]
    from stackowl.config.settings import ProviderConfig

    return ProviderConfig(
        name="NeraAiRaw", protocol="openai", enabled=True, api_key=None,
        base_url="http://llm-gateway.invalid:4000/v1", default_model="m",
        tiers=["fast"],
    )


async def test_the_contributor_no_longer_flattens_the_verdict(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``ProviderContributor`` mapped every non-ok result to "degraded". Even once
    the probe can say "down", that collapse would swallow it one layer up."""
    from stackowl.health import contributors
    from stackowl.startup import provider_probe

    async def _down(_cfg: object) -> ProviderResult:
        return _result("down", "unreachable")

    monkeypatch.setattr(provider_probe, "probe_provider", _down)
    status = await contributors.ProviderContributor(_cfg()).health_check()
    assert status.status == "down"


@pytest.mark.tripwire
def test_provider_health_is_not_wired_into_the_watchdog() -> None:
    """THE GUARD THAT MAKES "down" SAFE, and it must never quietly change.

    ``aggregator.is_live()`` is False whenever any subsystem is down, and the
    watchdog stops pinging systemd when liveness fails — so registering a
    provider on the LIVENESS aggregator would turn every VPN blip into a systemd
    restart loop. The orchestrator says so in as many words. This asserts the
    wiring, not the comment."""
    import inspect

    from stackowl.startup import orchestrator

    src = inspect.getsource(orchestrator._build_liveness_aggregator)
    assert "ProviderContributor" not in src, (
        "a provider was registered on the LIVENESS aggregator — an unreachable "
        "provider now reports 'down', so this would restart the process in a "
        "loop for as long as the network is out"
    )
    assert "DbContributor" in src and "FilesystemContributor" in src
