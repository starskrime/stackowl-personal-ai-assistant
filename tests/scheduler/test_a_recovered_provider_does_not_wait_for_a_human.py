"""A provider that came back must be noticed without anyone typing a message.

REPORTED BY THE OPERATOR, 2026-09-05: "that is a bug platform should not need to
restart to detect llm alive". He was right, and the precise shape is narrower and
worse than "it needs a restart".

WHAT ALREADY WORKED. `_maybe_reprobe_providers` exists and is wired
(`startup/orchestrator.py:2440`); its own docstring records the original defect —
"`_phase_providers` sets `self._providers_degraded` ONCE at boot and nothing ever
revisited it afterward, so a provider that came back up [went undetected] even though
the provider was healthy again". So the latch DOES clear itself.

WHAT DID NOT. It clears only inside `_dispatch_turn`, the inbound-MESSAGE path. Every
other turn — a scheduled job, the durable task loop, the RCA lane — reads the
`providers_degraded` value stamped onto `StepServices` at boot
(`orchestrator.py:1676`) and floors. So the platform can self-heal a provider outage
ONLY IF A HUMAN TALKS TO IT, which is precisely backwards for a platform whose stated
purpose is unattended work: the moment recovery matters most is the moment nobody is
there to trigger it.

AND THE ANSWER WAS ALREADY IN THE BUILDING. `ProviderContributor.health_check()`
probes every configured provider on the 5-minute health sweep and returns a
`HealthStatus`. On 2026-09-05 the VPN was down for hours; that contributor kept
probing, kept getting an answer, and the latch stayed set because nothing connected
the two. Six turns floored on it. One component knew, another needed it, nothing
joined them — this repo's most-recorded defect shape.

SO THE SWEEP CLEARS THE LATCH. Not a second prober: the sweep already probes, already
runs, already aggregates. This only stops it throwing the answer away.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.health.status import HealthStatus
from stackowl.scheduler.handlers.health_sweep import clear_degraded_if_a_provider_is_back


def _status(name: str, status: str) -> HealthStatus:
    return HealthStatus(name=name, status=status, message="", latency_ms=1.0)


class _Services:
    def __init__(self, degraded: bool) -> None:
        self.providers_degraded = degraded


def test_a_healthy_provider_clears_the_latch() -> None:
    """The case the operator hit: the provider is back, nobody has typed anything."""
    svc = _Services(degraded=True)

    cleared = clear_degraded_if_a_provider_is_back(
        [_status("provider:NeraAiRaw", "ok"), _status("db", "ok")], svc
    )

    assert cleared is True
    assert svc.providers_degraded is False


def test_a_provider_still_down_leaves_the_latch_set() -> None:
    """Recovery must be evidenced, never assumed — the whole point is that the
    sweep ASKED and got an answer."""
    svc = _Services(degraded=True)

    cleared = clear_degraded_if_a_provider_is_back(
        [_status("provider:NeraAiRaw", "down"), _status("db", "ok")], svc
    )

    assert cleared is False
    assert svc.providers_degraded is True


def test_no_provider_contributor_means_no_verdict() -> None:
    """FAILS CLOSED. A sweep with no provider status has learned nothing about
    providers, and 'everything else is healthy' is not evidence that the LLM is."""
    svc = _Services(degraded=True)

    cleared = clear_degraded_if_a_provider_is_back([_status("db", "ok")], svc)

    assert cleared is False
    assert svc.providers_degraded is True


def test_a_degraded_provider_is_not_good_enough() -> None:
    """`degraded` is not `ok`. The probe distinguishes them deliberately — the
    contributor's own comment records that collapsing the two was a past defect."""
    svc = _Services(degraded=True)

    assert clear_degraded_if_a_provider_is_back(
        [_status("provider:NeraAiRaw", "degraded")], svc
    ) is False
    assert svc.providers_degraded is True


def test_it_does_nothing_when_not_degraded() -> None:
    """No spurious work or log on the overwhelmingly common healthy sweep."""
    svc = _Services(degraded=False)

    assert clear_degraded_if_a_provider_is_back(
        [_status("provider:NeraAiRaw", "ok")], svc
    ) is False
    assert svc.providers_degraded is False


def test_the_recovery_is_announced_at_INFO(caplog: pytest.LogCaptureFixture) -> None:
    """Production runs at INFO. A latch clearing itself silently is indistinguishable
    from one that never cleared, which is the state this fix exists to end."""
    svc = _Services(degraded=True)

    with caplog.at_level(logging.INFO):
        clear_degraded_if_a_provider_is_back([_status("provider:NeraAiRaw", "ok")], svc)

    hits = [r for r in caplog.records if "no longer degraded" in r.getMessage().lower()]
    assert hits, "the platform resumed serving turns and said nothing"
    assert hits[0].levelno >= logging.INFO


def test_missing_services_never_raises() -> None:
    """This runs inside the scheduler's health sweep. A recovery helper that raises
    would wedge the sweep that every other subsystem's alerting depends on."""
    assert clear_degraded_if_a_provider_is_back([_status("provider:x", "ok")], None) is False


@pytest.mark.tripwire
def test_the_sweep_actually_calls_it() -> None:
    """Built-but-not-wired is this repo's most expensive recurring defect, and the
    bug being fixed here is a milder version of it: a capability that existed and
    was only reachable down one path."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "stackowl" / "scheduler" / "handlers" / "health_sweep.py"
    ).read_text(encoding="utf-8")

    assert "clear_degraded_if_a_provider_is_back(" in src, (
        "the health sweep does not clear the degraded latch — a recovered provider "
        "would again wait for a human to send a message"
    )
