"""A probe that failed is not a measurement, and must not outlive the outage.

``resolve_window`` probes the provider for its real context window and memoizes
the answer. When the probe fails it uses ``DEFAULT_WINDOW_FALLBACK`` (100,000)
and says so plainly::

    [model_window] probe FAILED — using the probe-failure floor, so this
    model's real window is unknown

and then, two lines further down, stores that unknown in the same cache as a
measurement — ``_WINDOW_CACHE[key] = w`` runs on every path.

NOTHING BRINGS IT BACK. The rejection self-correction only ever lowers a window
("a rejection can prove a window is too SMALL, never too large"), and
``invalidate`` is called from exactly one place: ``registry.apply_settings``,
for providers added, rotated or removed on a config reload. A provider that
simply RECOVERS is not a config change, so the floor survives for the life of
the process.

MEASURED 2026-09-04, during an 18-hour outage: every ``[pipeline] execute:
context budget`` line reports ``model_window: 100000`` — the floor exactly, not a
probed value — and "probe FAILED" appears in the same window. Every process
booted during the outage will keep believing that after the provider returns,
until someone restarts it.

THIS IS THE SAME CAUSE AS THE host_locality FIX EARLIER TODAY, in a second place:
a transient failure written into a cache that only ever holds determinations.
There the cost was billing a private host at the cloud rate; here it is running
against a floor instead of the real window, sizing history and ``_output_cap``
against it. Both had the same fix available and only one had taken it — which is
the "what else does this cause reach" question that item should have asked.

THE ASYMMETRY IS THE FIX, not a TTL. A MEASURED window is a fact about the model
that does not change while the process runs, and still caches for its lifetime. A
floor reached because the probe could not run is PROVISIONAL, and the moment the
provider is known healthy again it must be dropped so the next resolve re-probes.

WHY RECOVERY AND NOT A RETRY EVERY CALL. Unlike a DNS lookup, this probe is an
HTTP round trip; re-probing on every resolve would pay a timeout per turn for the
whole outage. The circuit breaker already proves recovery exactly once, at
``HALF_OPEN -> CLOSED (probe succeeded)`` — an event that means "the provider
answered". Hanging the invalidation there costs nothing during the outage and
fires once when it ends.
"""

from __future__ import annotations

import pytest

from stackowl.providers import model_window
from stackowl.providers.circuit_breaker import CircuitBreaker, CircuitState

PROVIDER = "NeraAiRaw"
MODEL = "neraai-v1-raw"
KEY = (PROVIDER, MODEL)


@pytest.fixture(autouse=True)
def _clean_cache():
    model_window.reset_window_cache()
    model_window._PROVISIONAL.clear()
    yield
    model_window.reset_window_cache()
    model_window._PROVISIONAL.clear()


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


def test_a_recovered_provider_drops_the_probe_failure_floor() -> None:
    """THE DEFECT. The floor cached during the outage survived it, so the
    platform kept running against 100,000 after the provider came back."""
    model_window.remember_probe_failure(PROVIDER, MODEL, model_window.DEFAULT_WINDOW_FALLBACK)
    assert model_window.cached_window(PROVIDER, MODEL) == model_window.DEFAULT_WINDOW_FALLBACK

    model_window.invalidate_provisional(PROVIDER)

    assert model_window.cached_window(PROVIDER, MODEL) is None, (
        "a window the platform itself called unknown outlived the outage that "
        "produced it"
    )


def test_a_MEASURED_window_survives_recovery() -> None:
    """THE HALF THAT MUST NOT MOVE. A real probed window is a fact about the
    model; re-probing it on every recovery would pay an HTTP round trip for
    nothing, and this cache exists precisely to avoid that."""
    model_window._WINDOW_CACHE[KEY] = 262_144  # measured, not a floor

    model_window.invalidate_provisional(PROVIDER)

    assert model_window.cached_window(PROVIDER, MODEL) == 262_144


def test_one_providers_recovery_does_not_touch_another() -> None:
    """Recovery is per provider. Dropping every provisional window on any
    recovery would re-probe models that were never affected."""
    model_window.remember_probe_failure(PROVIDER, MODEL, 100_000)
    model_window.remember_probe_failure("OtherProvider", "m", 100_000)

    model_window.invalidate_provisional(PROVIDER)

    assert model_window.cached_window(PROVIDER, MODEL) is None
    assert model_window.cached_window("OtherProvider", "m") == 100_000


def test_the_floor_still_caches_DURING_the_outage() -> None:
    """No probe storm. Unlike a DNS lookup this probe is an HTTP round trip, so
    re-probing on every resolve would pay a timeout per turn for the whole
    outage. The floor stays cached until recovery is PROVEN."""
    model_window.remember_probe_failure(PROVIDER, MODEL, 100_000)
    assert model_window.cached_window(PROVIDER, MODEL) == 100_000
    assert model_window.cached_window(PROVIDER, MODEL) == 100_000


def test_a_later_real_measurement_stops_being_provisional() -> None:
    """Once the true window is learned the entry is no longer a guess, so a
    subsequent recovery must not throw it away."""
    model_window.remember_probe_failure(PROVIDER, MODEL, 100_000)
    model_window._WINDOW_CACHE[KEY] = 262_144
    model_window._PROVISIONAL.discard(KEY)

    model_window.invalidate_provisional(PROVIDER)

    assert model_window.cached_window(PROVIDER, MODEL) == 262_144


# --------------------------------------------------------------------------- #
# Wired, not merely available                                                  #
# --------------------------------------------------------------------------- #


def test_the_breaker_closing_actually_invalidates() -> None:
    """BUILT BUT NOT WIRED is the first shape on this codebase's own list, and
    this suite would pass just as happily with the call absent. The breaker's
    HALF_OPEN -> CLOSED transition is the one moment the platform KNOWS the
    provider answered, so the invalidation hangs there and is asserted here."""
    breaker = CircuitBreaker(provider_name=PROVIDER)
    model_window.remember_probe_failure(PROVIDER, MODEL, 100_000)

    breaker._state = CircuitState.HALF_OPEN  # noqa: SLF001 — the recovery moment
    breaker._record_success(CircuitState.HALF_OPEN)  # noqa: SLF001

    assert breaker._state is CircuitState.CLOSED  # noqa: SLF001
    assert model_window.cached_window(PROVIDER, MODEL) is None, (
        "the breaker proved the provider is healthy and the stale floor stayed"
    )


def test_an_ordinary_success_does_not_invalidate() -> None:
    """A success while already CLOSED is not a recovery — invalidating there
    would re-probe on ordinary traffic, which is what the cache exists to stop."""
    breaker = CircuitBreaker(provider_name=PROVIDER)
    model_window.remember_probe_failure(PROVIDER, MODEL, 100_000)

    breaker._record_success(CircuitState.CLOSED)  # noqa: SLF001

    assert model_window.cached_window(PROVIDER, MODEL) == 100_000


# --------------------------------------------------------------------------- #
# The path production actually takes                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_REAL_failure_path_marks_the_entry_provisional(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CAUGHT BY MUTATION. Every test above drives ``remember_probe_failure`` —
    the helper this change added — so deleting the marking from
    ``resolve_window``'s own failure branch left all seven of them green.

    A suite that exercises only the seam it introduced proves the seam, not the
    behaviour. This drives the branch production takes: a probe that returns
    nothing must cache the floor AND mark it provisional, or the recovery hook
    has nothing to drop."""
    async def _no_answer(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(model_window, "_probe_openai_compatible", _no_answer)

    window = await model_window.resolve_window(
        provider_name=PROVIDER, base_url="http://llm-gateway.invalid:4000/v1",
        model=MODEL, context_chars=None, protocol="openai", api_key="k",
    )

    assert window == model_window.DEFAULT_WINDOW_FALLBACK
    assert KEY in model_window._PROVISIONAL, (
        "resolve_window cached the probe-failure floor without marking it "
        "provisional, so recovery would never replace it with a measurement"
    )

    model_window.invalidate_provisional(PROVIDER)
    assert model_window.cached_window(PROVIDER, MODEL) is None


@pytest.mark.anyio
async def test_a_SUCCESSFUL_probe_is_not_provisional(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The other direction on the same real path: a probe that answered is a
    measurement and must survive every later recovery."""
    async def _answers(*_a: object, **_k: object) -> int:
        return 262_144

    monkeypatch.setattr(model_window, "_probe_openai_compatible", _answers)

    window = await model_window.resolve_window(
        provider_name=PROVIDER, base_url="http://llm-gateway.invalid:4000/v1",
        model=MODEL, context_chars=None, protocol="openai", api_key="k",
    )

    assert window == 262_144
    assert KEY not in model_window._PROVISIONAL
    model_window.invalidate_provisional(PROVIDER)
    assert model_window.cached_window(PROVIDER, MODEL) == 262_144
