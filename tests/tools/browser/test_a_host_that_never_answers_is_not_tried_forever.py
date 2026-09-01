"""A host that has not answered three times does not get a fourth 30-second wait.

MEASURED 2026-09-01 across the retained logs: **168 navigation timeouts across
only SEVENTEEN distinct hosts, and 151 of them (90%) were on a host that had
ALREADY timed out**::

    46  www.linkedin.com          17  boards-api.greenhouse.io
    32  api.lever.co              15  job-boards.greenhouse.io
    29  api.ashbyhq.com            6  jobs.lever.co

Every one waits the full 30s budget, so www.linkedin.com alone cost about 23
minutes of wall-clock — and a model round each time — on a host that had not
once responded. The platform had no memory of it: every navigation was its
first. That is the same "re-enter the identical path" shape as the retry loop,
one layer below it.

IT IS NOT A WAIT-STRATEGY MISTAKE, which was the obvious first guess and is
recorded here as refuted: 149 of 168 timeouts were already waiting on
``domcontentloaded``, the fastest condition available. The pages genuinely never
delivered a DOM.

THE PLATFORM'S OWN RCA ASKED FOR THIS, and in the right order: "(4) Before
hard-coding the per-host circuit-break, collect the per-call host/status/error
data — per-host failure attribution is a documented gap and the circuit-break as
specified is not yet evidence-anchored." The measurement above is that evidence,
which is why the breaker ships now and not then.

NO SECOND ENGINE. ``providers/circuit_breaker.CircuitBreaker`` is a general
per-key failure state machine with a half-open probe and the FX-02 doubling
backoff already in it. Only the key is new. Building a second breaker for hosts
would be the duplication this codebase has paid for repeatedly.

THE WINDOW IS DERIVED, NOT CHOSEN. Consecutive timeouts on one host arrive a
median 31 seconds apart (p75 158s) and **142 of 147 gaps are under 900s**, so a
900-second window catches 96.6% of the repeats. The breaker's own 60s default
would have caught barely half — which is exactly the kind of constant that gets
picked by taste and then quietly does nothing.

WHAT IT MUST NEVER DO is refuse a working host, so recovery is the breaker's own
half-open probe, ``unknown_host`` is excluded (a DNS failure is a property of the
URL, not a host under load), and every helper fails CLOSED: a broken breaker
lets the navigation through rather than blocking it.
"""

from __future__ import annotations

import pytest

from stackowl.tools.browser import browse

pytestmark = pytest.mark.asyncio

_LINKEDIN = "https://www.linkedin.com/jobs/view/1"
_WORKING = "https://example.com/page"


@pytest.fixture(autouse=True)
def _clean_breakers():  # noqa: ANN202
    """Process-wide registry — isolate every test from every other."""
    browse._host_breakers.clear()
    yield
    browse._host_breakers.clear()


async def _fail(url: str, kind: str = "timeout", times: int = 1) -> None:
    for _ in range(times):
        await browse.record_host_outcome(url, kind=kind)


async def test_a_working_host_is_never_refused() -> None:
    """The expensive direction, first. A wrong OPEN here silently removes a
    working source from the platform."""
    assert browse.host_is_open_circuit(_WORKING) is False
    await browse.record_host_outcome(_WORKING, kind=None)
    assert browse.host_is_open_circuit(_WORKING) is False


async def test_two_failures_are_not_enough() -> None:
    """A transient blip must not cost a host its next attempt."""
    await _fail(_LINKEDIN, times=2)
    assert browse.host_is_open_circuit(_LINKEDIN) is False


async def test_the_fourth_attempt_is_refused() -> None:
    """The measured case: LinkedIn timed out 46 times at 30s each."""
    await _fail(_LINKEDIN, times=3)
    assert browse.host_is_open_circuit(_LINKEDIN) is True


async def test_a_connection_reset_counts_too() -> None:
    """Same meaning as a timeout: the host did not answer."""
    await _fail(_LINKEDIN, kind="connection_reset", times=3)
    assert browse.host_is_open_circuit(_LINKEDIN) is True


async def test_an_unknown_host_never_trips_the_breaker() -> None:
    """DNS failure is a property of the URL, not of a host under load. Tripping
    on it would suppress a typo'd domain instead of an unresponsive one."""
    await _fail(_LINKEDIN, kind="unknown_host", times=5)
    assert browse.host_is_open_circuit(_LINKEDIN) is False


async def test_a_navigation_failure_of_another_kind_does_not_trip_it() -> None:
    """A page that loaded and disappointed is not the host being down."""
    await _fail(_LINKEDIN, kind="navigation_failed", times=5)
    assert browse.host_is_open_circuit(_LINKEDIN) is False


async def test_hosts_are_independent() -> None:
    """One dead host must not close the platform's eyes to every other."""
    await _fail(_LINKEDIN, times=3)
    assert browse.host_is_open_circuit(_LINKEDIN) is True
    assert browse.host_is_open_circuit(_WORKING) is False


async def test_the_host_key_ignores_case_and_path() -> None:
    """Two spellings of one host must share a breaker, or it never trips."""
    assert browse._host_of("https://WWW.LinkedIn.com/jobs/view/1") == "www.linkedin.com"
    await _fail("https://WWW.LinkedIn.com/a", times=3)
    assert browse.host_is_open_circuit("https://www.linkedin.com/b") is True


async def test_a_url_with_no_host_is_not_breakable() -> None:
    assert browse._host_of("not-a-url") == ""
    assert browse.host_is_open_circuit("not-a-url") is False
    await browse.record_host_outcome("not-a-url", kind="timeout")  # must not raise


async def test_a_broken_breaker_lets_the_navigation_through(monkeypatch) -> None:  # noqa: ANN001
    """Fails CLOSED. A breaker that blocked browsing because of its own error
    would be a worse failure than the one it exists to prevent."""

    def _boom(host: str) -> object:
        raise RuntimeError("registry is unhappy")

    monkeypatch.setattr(browse, "_breaker_for", _boom)
    assert browse.host_is_open_circuit(_LINKEDIN) is False
    await browse.record_host_outcome(_LINKEDIN, kind="timeout")  # must not raise


def test_the_window_is_the_measured_one_and_says_why() -> None:
    """The number alone is not the fix — a later reader restoring the breaker's
    60s default would silently halve the coverage, so the measurement that
    justifies 900 must live beside it."""
    import inspect

    assert browse._HOST_BREAKER_WINDOW_SECONDS == 900
    assert browse._HOST_BREAKER_THRESHOLD == 3
    marker = inspect.getsource(browse).split("_HOST_BREAKER_WINDOW_SECONDS = 900")[0]
    assert "142 of 147" in marker and "151" in marker, (
        "the measurement that justifies the window is not stated next to it"
    )


def test_it_reuses_the_existing_breaker() -> None:
    """Structural: a second failure state machine for hosts would be the
    duplication this codebase has paid for repeatedly."""
    import inspect

    source = inspect.getsource(browse)
    assert "from stackowl.providers.circuit_breaker import CircuitBreaker" in source
    assert "class " not in source.split("_breaker_for")[1][:400], (
        "a bespoke breaker is being defined instead of reusing the existing one"
    )
