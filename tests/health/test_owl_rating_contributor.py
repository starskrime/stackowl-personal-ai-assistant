"""OwlRatingHealthContributor — owl-layer health was completely invisible to
the health-aggregator/incident-escalation pipeline before this (no
contributor was keyed on an owl at all). Dislike votes only ever suppressed
DNA-attribution reinforcement; they never aggregated into any health signal.
"""

from __future__ import annotations

import pytest

from stackowl.health.contributors import OwlRatingHealthContributor


class _FakeManifest:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def list(self) -> list[_FakeManifest]:
        return [_FakeManifest(n) for n in self._names]


class _FakeOutcomeStore:
    def __init__(self, counts: dict[str, tuple[int, int]]) -> None:
        self._counts = counts

    async def count_approach_ratings_for_owl(
        self, owl_name: str, *, since_epoch: float = 0.0,
    ) -> tuple[int, int]:
        return self._counts.get(owl_name, (0, 0))


class _BoomOutcomeStore:
    async def count_approach_ratings_for_owl(
        self, owl_name: str, *, since_epoch: float = 0.0,
    ) -> tuple[int, int]:
        raise RuntimeError("db unreachable")


@pytest.mark.asyncio
async def test_ok_when_no_owl_over_threshold() -> None:
    store = _FakeOutcomeStore({"scout": (18, 2)})  # 2/20 = 10% dislike
    contributor = OwlRatingHealthContributor(store, _FakeRegistry(["scout"]))  # type: ignore[arg-type]

    status = await contributor.health_check()

    assert status.status == "ok"
    assert status.name == "owl_ratings"


@pytest.mark.asyncio
async def test_degraded_when_dislike_rate_crosses_threshold() -> None:
    store = _FakeOutcomeStore({"scout": (4, 6)})  # 6/10 = 60% dislike
    contributor = OwlRatingHealthContributor(store, _FakeRegistry(["scout"]))  # type: ignore[arg-type]

    status = await contributor.health_check()

    assert status.status == "degraded"
    assert "scout" in (status.message or "")


@pytest.mark.asyncio
async def test_below_min_samples_never_flags_even_at_100_percent_dislike() -> None:
    # Only 3 votes total — below the default min_samples=10, must not flag
    # a brand-new owl's very first (unlucky) handful of votes.
    store = _FakeOutcomeStore({"scout": (0, 3)})
    contributor = OwlRatingHealthContributor(store, _FakeRegistry(["scout"]))  # type: ignore[arg-type]

    status = await contributor.health_check()

    assert status.status == "ok"


@pytest.mark.asyncio
async def test_one_owls_query_failure_does_not_sink_the_check() -> None:
    class _MixedStore:
        async def count_approach_ratings_for_owl(
            self, owl_name: str, *, since_epoch: float = 0.0,
        ) -> tuple[int, int]:
            if owl_name == "broken":
                raise RuntimeError("boom")
            return (2, 8)  # 80% dislike, over threshold

    contributor = OwlRatingHealthContributor(
        _MixedStore(), _FakeRegistry(["broken", "scout"])  # type: ignore[arg-type]
    )

    status = await contributor.health_check()

    assert status.status == "degraded"
    assert "scout" in (status.message or "")


@pytest.mark.asyncio
async def test_custom_thresholds_respected() -> None:
    store = _FakeOutcomeStore({"scout": (8, 2)})  # 20% dislike
    contributor = OwlRatingHealthContributor(
        store, _FakeRegistry(["scout"]), min_samples=5, degraded_threshold=0.15,  # type: ignore[arg-type]
    )

    status = await contributor.health_check()

    assert status.status == "degraded"
