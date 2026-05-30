"""Unit tests for the web_search package: WebSearchProvider ABC + WebSearchRegistry.

All providers here are FAKE in-memory implementations — no httpx, no network. They
exercise the registry's precedence walk, self-healing cascade, explicit-provider
resolution, buggy-availability isolation, and the TTL cache.
"""

from __future__ import annotations

from stackowl.web_search.base import (
    WebHit,
    WebSearchProvider,
    WebSearchResult,
    failure_result,
    success_result,
)
from stackowl.web_search.registry import WebSearchRegistry


class FakeClock:
    """Injectable monotonic clock — starts at 0, advanced explicitly in tests."""

    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeProvider(WebSearchProvider):
    """A configurable in-memory provider.

    - ``available``: what is_available() returns.
    - ``hits``: number of web hits to return on a successful search.
    - ``behavior``: "ok" | "raise" | "empty" | "failure" — what search() does.
    - ``succeed_after``: if set, the first N calls follow ``behavior`` then it
      switches to "ok" (used to test retry-once recovery).
    - ``calls``: how many times search() was invoked (cache-hit detection).
    """

    def __init__(
        self,
        name: str,
        *,
        available: bool = True,
        hits: int = 2,
        behavior: str = "ok",
        succeed_after: int | None = None,
    ) -> None:
        self._name = name
        self._available = available
        self._hits = hits
        self._behavior = behavior
        self._succeed_after = succeed_after
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    async def search(self, query: str, limit: int) -> WebSearchResult:
        self.calls += 1
        effective = self._behavior
        if self._succeed_after is not None and self.calls > self._succeed_after:
            effective = "ok"

        if effective == "raise":
            raise RuntimeError(f"{self._name} boom")
        if effective == "failure":
            return failure_result(f"{self._name} reported failure")
        if effective == "empty":
            return success_result([])

        hits = [
            WebHit(
                title=f"{self._name} #{i}",
                url=f"https://{self._name}.test/{i}",
                description=f"result {i} from {self._name}",
                position=i,
            )
            for i in range(1, min(self._hits, limit) + 1)
        ]
        return success_result(hits)


class BuggyAvailableProvider(FakeProvider):
    """is_available() RAISES — must not kill registry resolution."""

    def is_available(self) -> bool:
        raise RuntimeError("availability probe exploded")


def _make_registry(*providers: WebSearchProvider, clock: FakeClock | None = None) -> WebSearchRegistry:
    return WebSearchRegistry(list(providers), ttl_seconds=900.0, time_fn=clock or FakeClock())


# --------------------------------------------------------------------------- #
# Frozen result shape contract                                                #
# --------------------------------------------------------------------------- #


async def test_success_result_shape_matches_frozen_contract() -> None:
    hits = [WebHit(title="t", url="https://x.test", description="d", position=1)]
    result = success_result(hits)
    payload = result.to_dict()
    assert payload == {
        "success": True,
        "data": {"web": [{"title": "t", "url": "https://x.test", "description": "d", "position": 1}]},
    }


async def test_failure_result_shape_matches_frozen_contract() -> None:
    result = failure_result("provider 'brave' not configured")
    payload = result.to_dict()
    assert payload == {
        "success": False,
        "data": {"web": []},
        "error": "provider 'brave' not configured",
    }


# --------------------------------------------------------------------------- #
# Precedence                                                                   #
# --------------------------------------------------------------------------- #


async def test_returns_highest_precedence_available_provider() -> None:
    primary = FakeProvider("searxng", hits=1)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, secondary)

    result = await reg.search("hello", limit=5)

    payload = result.to_dict()
    assert payload["success"] is True
    assert payload["data"]["web"][0]["url"] == "https://searxng.test/1"
    assert primary.calls == 1
    assert secondary.calls == 0


async def test_primary_unavailable_cascades_to_next_available() -> None:
    primary = FakeProvider("searxng", available=False)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, secondary)

    result = await reg.search("hello", limit=5)

    assert result.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert primary.calls == 0
    assert secondary.calls == 1


async def test_all_unavailable_returns_structured_unavailable() -> None:
    reg = _make_registry(
        FakeProvider("searxng", available=False),
        FakeProvider("brave", available=False),
    )

    result = await reg.search("hello", limit=5)
    payload = result.to_dict()

    assert payload["success"] is False
    assert payload["data"]["web"] == []
    assert "unavailable" in payload["error"]


# --------------------------------------------------------------------------- #
# Fix C — distinguish "not configured" from "transient/throttled"              #
# --------------------------------------------------------------------------- #


async def test_no_provider_available_returns_not_configured_message() -> None:
    # NONE available (nothing configured / ddgs not importable) → actionable "not
    # configured" guidance that names the /config knobs.
    reg = _make_registry(
        FakeProvider("searxng", available=False),
        FakeProvider("brave", available=False),
        FakeProvider("ddg", available=False),
    )

    result = await reg.search("hello", limit=5)
    payload = result.to_dict()

    assert payload["success"] is False
    assert payload["data"]["web"] == []
    error = payload["error"]
    assert "not configured" in error
    assert "web_search.searxng_base_url" in error
    assert "web_search.brave_api_key" in error
    assert "/config" in error


async def test_available_but_all_failing_returns_transient_message() -> None:
    # At least one provider AVAILABLE but all error/rate-limit (raise / success=False after
    # retry) → "temporarily unavailable / try again" guidance, NOT the "not configured" one.
    reg = _make_registry(
        FakeProvider("searxng", behavior="raise"),
        FakeProvider("brave", behavior="failure"),
    )

    result = await reg.search("hello", limit=5)
    payload = result.to_dict()

    assert payload["success"] is False
    assert payload["data"]["web"] == []
    error = payload["error"]
    assert "temporarily unavailable" in error
    assert "Try again" in error
    assert "web_search.searxng_base_url" in error
    assert "/config" in error
    assert "not configured" not in error


async def test_fix_c_two_messages_differ() -> None:
    # The two exhausted-cascade messages must be DISTINCT (the whole point of Fix C) and
    # each must name a /config knob so the guidance is actionable + discoverable.
    not_configured = await _make_registry(
        FakeProvider("searxng", available=False),
        FakeProvider("ddg", available=False),
    ).search("hello", limit=5)
    transient = await _make_registry(
        FakeProvider("searxng", behavior="raise"),
    ).search("hello", limit=5)

    msg_unconfigured = not_configured.to_dict()["error"]
    msg_transient = transient.to_dict()["error"]
    assert msg_unconfigured != msg_transient
    assert "/config" in msg_unconfigured
    assert "/config" in msg_transient


# --------------------------------------------------------------------------- #
# Explicit provider selection                                                 #
# --------------------------------------------------------------------------- #


async def test_explicit_provider_unavailable_returns_precise_error_no_switch() -> None:
    primary = FakeProvider("searxng", hits=1)  # available, but NOT requested
    brave = FakeProvider("brave", available=False)
    reg = _make_registry(primary, brave)

    result = await reg.search("hello", limit=5, provider="brave")
    payload = result.to_dict()

    assert payload["success"] is False
    assert "brave" in payload["error"]
    assert "not configured" in payload["error"]
    # Must NOT silently switch to the available searxng.
    assert primary.calls == 0
    assert brave.calls == 0


async def test_explicit_provider_unknown_returns_precise_error() -> None:
    reg = _make_registry(FakeProvider("searxng"))

    result = await reg.search("hello", provider="nonexistent")
    payload = result.to_dict()

    assert payload["success"] is False
    assert "nonexistent" in payload["error"]


async def test_explicit_available_provider_used_exclusively() -> None:
    primary = FakeProvider("searxng", hits=1)
    brave = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, brave)

    result = await reg.search("hello", limit=5, provider="brave")

    assert result.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert primary.calls == 0
    assert brave.calls == 1


# --------------------------------------------------------------------------- #
# Self-healing cascade (retry-once then advance)                              #
# --------------------------------------------------------------------------- #


async def test_retry_once_recovers_on_same_provider() -> None:
    # First call raises, second call (retry) succeeds — never advances.
    flaky = FakeProvider("searxng", behavior="raise", succeed_after=1, hits=1)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(flaky, secondary)

    result = await reg.search("hello", limit=5)

    assert result.to_dict()["data"]["web"][0]["url"] == "https://searxng.test/1"
    assert flaky.calls == 2  # initial + one retry
    assert secondary.calls == 0


async def test_persistent_failure_advances_to_secondary() -> None:
    # Primary always raises (retry-once still fails) → advance to brave.
    primary = FakeProvider("searxng", behavior="raise", hits=1)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, secondary)

    result = await reg.search("hello", limit=5)

    assert result.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert primary.calls == 2  # initial + retry, both failed
    assert secondary.calls == 1


async def test_empty_result_is_terminal_success_no_cascade() -> None:
    # A success=True result with zero hits is a TERMINAL answer (the query simply has no
    # results) — it must be returned honestly, NOT retried and NOT cascaded past.
    primary = FakeProvider("searxng", behavior="empty", hits=0)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, secondary)

    result = await reg.search("hello", limit=5)
    payload = result.to_dict()

    assert payload["success"] is True
    assert payload["data"]["web"] == []
    assert primary.calls == 1  # called ONCE — no retry
    assert secondary.calls == 0  # no cascade to a lower-precedence provider


async def test_failure_result_treated_as_failure_and_advances() -> None:
    primary = FakeProvider("searxng", behavior="failure", hits=1)
    secondary = FakeProvider("brave", hits=1)
    reg = _make_registry(primary, secondary)

    result = await reg.search("hello", limit=5)

    assert result.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert secondary.calls == 1


async def test_all_providers_fail_returns_structured_unavailable() -> None:
    reg = _make_registry(
        FakeProvider("searxng", behavior="raise"),
        FakeProvider("brave", behavior="raise"),
    )

    result = await reg.search("hello", limit=5)
    payload = result.to_dict()

    assert payload["success"] is False
    assert payload["data"]["web"] == []
    assert "unavailable" in payload["error"]


# --------------------------------------------------------------------------- #
# Buggy is_available isolation                                                 #
# --------------------------------------------------------------------------- #


async def test_buggy_is_available_does_not_kill_resolution() -> None:
    buggy = BuggyAvailableProvider("searxng", hits=1)
    healthy = FakeProvider("brave", hits=1)
    reg = _make_registry(buggy, healthy)

    result = await reg.search("hello", limit=5)

    # Buggy provider is skipped (treated as unavailable); brave answers.
    assert result.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert buggy.calls == 0
    assert healthy.calls == 1


# --------------------------------------------------------------------------- #
# TTL cache                                                                    #
# --------------------------------------------------------------------------- #


async def test_identical_search_hits_cache_provider_called_once() -> None:
    primary = FakeProvider("searxng", hits=1)
    clock = FakeClock()
    reg = _make_registry(primary, clock=clock)

    first = await reg.search("hello", limit=5)
    second = await reg.search("hello", limit=5)

    assert first.to_dict() == second.to_dict()
    assert primary.calls == 1  # second served from cache


async def test_cache_expires_after_ttl() -> None:
    primary = FakeProvider("searxng", hits=1)
    clock = FakeClock()
    reg = _make_registry(primary, clock=clock)

    await reg.search("hello", limit=5)
    clock.advance(901.0)  # past 900s TTL
    await reg.search("hello", limit=5)

    assert primary.calls == 2


async def test_cache_miss_on_different_query() -> None:
    primary = FakeProvider("searxng", hits=1)
    reg = _make_registry(primary)

    await reg.search("hello", limit=5)
    await reg.search("world", limit=5)

    assert primary.calls == 2


async def test_cache_miss_on_different_limit() -> None:
    primary = FakeProvider("searxng", hits=3)
    reg = _make_registry(primary)

    await reg.search("hello", limit=2)
    await reg.search("hello", limit=5)

    assert primary.calls == 2


async def test_cache_key_includes_resolved_provider() -> None:
    # Same query/limit, but explicit provider differs → distinct cache entries.
    searxng = FakeProvider("searxng", hits=1)
    brave = FakeProvider("brave", hits=1)
    reg = _make_registry(searxng, brave)

    await reg.search("hello", limit=5, provider="searxng")
    await reg.search("hello", limit=5, provider="brave")

    assert searxng.calls == 1
    assert brave.calls == 1


async def test_failed_searches_are_not_cached() -> None:
    reg = _make_registry(FakeProvider("searxng", available=False))

    await reg.search("hello", limit=5)
    await reg.search("hello", limit=5)
    # Nothing to assert on calls (provider unavailable); the point is no crash and
    # the failure shape is returned both times (not a stale cached success).
    result = await reg.search("hello", limit=5)
    assert result.to_dict()["success"] is False


async def test_recovered_higher_precedence_provider_beats_stale_lower_cache() -> None:
    # M1 regression: cache must key on the WINNING provider, never the available-chain.
    # searxng (precedence #1) is DOWN (success=False both attempts) → cascade serves brave,
    # whose answer is cached under (query,"brave",limit). searxng then RECOVERS. A second
    # identical search must call searxng again (checked first in the walk) and return
    # SEARXNG's fresh answer — NOT the stale brave cache entry.
    #
    # succeed_after=2: calls 1 & 2 (initial + retry on the first search) fail → cascade;
    # call 3 (the second search) flips to "ok" with hits.
    searxng = FakeProvider("searxng", behavior="failure", succeed_after=2, hits=1)
    brave = FakeProvider("brave", hits=1)
    clock = FakeClock()
    reg = _make_registry(searxng, brave, clock=clock)

    first = await reg.search("hello", limit=5)
    assert first.to_dict()["data"]["web"][0]["url"] == "https://brave.test/1"
    assert searxng.calls == 2  # initial + retry, both success=False
    assert brave.calls == 1

    # searxng has recovered; a second identical search must prefer it over brave's cache.
    second = await reg.search("hello", limit=5)
    assert second.to_dict()["data"]["web"][0]["url"] == "https://searxng.test/1"
    assert searxng.calls == 3  # searxng was searched again (not shadowed by brave cache)
    assert brave.calls == 1  # brave NOT re-consulted while searxng answers


async def test_empty_success_is_not_cached() -> None:
    # m1 regression: a single available provider returns success-empty → success=True,
    # web=[], provider called ONCE (no retry/cascade), and the empty result is NOT cached
    # (a second identical call calls the provider again).
    primary = FakeProvider("searxng", behavior="empty", hits=0)
    clock = FakeClock()
    reg = _make_registry(primary, clock=clock)

    first = await reg.search("hello", limit=5)
    assert first.to_dict()["success"] is True
    assert first.to_dict()["data"]["web"] == []
    assert primary.calls == 1  # no retry, no cascade
    assert reg.cache_size() == 0  # empty result NOT cached

    await reg.search("hello", limit=5)
    assert primary.calls == 2  # re-queried (no stale empty cache hit)


async def test_limit_clamped_low_and_high() -> None:
    # m2: limit is clamped to [1, 50] before dispatch and used for the provider call.
    high = FakeProvider("searxng", hits=999)
    reg = _make_registry(high)
    result = await reg.search("hello", limit=10_000)
    # provider received a clamped limit of 50 → at most 50 hits.
    assert len(result.to_dict()["data"]["web"]) == 50

    for bad in (0, -5):
        low = FakeProvider("searxng", hits=999)
        reg2 = _make_registry(low)
        res = await reg2.search("hello", limit=bad)
        # clamped up to 1 → exactly one hit.
        assert len(res.to_dict()["data"]["web"]) == 1


async def test_non_int_limit_coerced_to_default() -> None:
    # m2: a non-int / None limit coerces safely to the default (5) — never raises.
    for bad in (None, "5", 3.0, True):
        prov = FakeProvider("searxng", hits=999)
        reg = _make_registry(prov)
        result = await reg.search("hello", limit=bad)  # type: ignore[arg-type]
        assert result.to_dict()["success"] is True
        assert len(result.to_dict()["data"]["web"]) == 5  # default limit applied


async def test_clamped_limit_used_for_cache_key() -> None:
    # m2: absurd distinct limits that clamp to the same value share ONE cache entry.
    primary = FakeProvider("searxng", hits=1)
    reg = _make_registry(primary)

    await reg.search("hello", limit=10_000)  # clamps to 50
    await reg.search("hello", limit=99_999)  # also clamps to 50 → cache hit

    assert primary.calls == 1
    assert reg.cache_size() == 1


async def test_cache_is_bounded() -> None:
    primary = FakeProvider("searxng", hits=1)
    reg = WebSearchRegistry([primary], ttl_seconds=900.0, max_entries=8, time_fn=FakeClock())

    for i in range(50):
        await reg.search(f"query-{i}", limit=5)

    assert reg.cache_size() <= 8
