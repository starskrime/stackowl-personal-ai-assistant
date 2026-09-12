"""Refusing a caller that is guessing — the login route's only brake.

DEBT-310 shipped `POST /api/v1/login` and named this hole in its own record:
*"NO RATE LIMIT. A login endpoint invites brute force ... On loopback with a
43-character token behind it the exposure is this machine only. It becomes real
the moment ESC-172 is answered 'widen the bind', and it is named here so that
answer carries this with it rather than discovering it afterwards."* The operator
answered it on 2026-09-12 — bind every interface, work out of the box — so this
ships in the same change as the widening rather than after it.

**IT IS NOT `providers/rate_limiter.py`, AND THAT IS A DESIGN DECISION, NOT AN
OVERSIGHT.** That class is a token bucket whose `acquire()` SLEEPS: it paces
calls WE make outbound so a provider does not refuse us. Pointing it at inbound
requests inverts its purpose and is actively harmful — a sleeping handler holds a
worker, so an attacker who can make the server wait has been handed an
amplifier instead of a brake. The correct shape for an inbound guard is to
REFUSE immediately and cheaply, which is what this does. "Find the existing loop
and extend it" asks whether the existing thing does this job; this one does the
opposite job.

**THE COUNTER IS BOUNDED BECAUSE ITS KEY IS ATTACKER-CONTROLLED.** A dict keyed
by source address with no cap is itself the denial of service it exists to
prevent — an attacker rotating source addresses grows it without limit. So the
map is capped and evicts the least recently seen entry, and the cap is stated
rather than assumed safe.

WHAT IT IS WORTH, HONESTLY. Against the shipped `admin`/`admin` it is worth
nothing — the first guess wins, which is why the platform warns about that
default at boot and the page banners it after every sign-in. It protects a
password the operator HAS changed, which is the only thing it can protect.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from stackowl.infra.observability import log

#: Failures from one source before it is refused. Generous for a person
#: mistyping a password, useless for a machine trying a word list.
MAX_FAILURES = 10

#: How long a source's failures are remembered, and how long a refusal lasts.
#: One window for both so there is a single number to reason about.
WINDOW_SECONDS = 300.0

#: How many distinct sources are tracked. The key is attacker-controlled, so the
#: map must not grow without bound; at the cap the least recently seen entry is
#: evicted. An attacker with 2,049 addresses can therefore push their own earlier
#: failures out — that is the accepted cost of a bounded map, and it is written
#: down rather than discovered: someone with two thousand addresses is not being
#: stopped by an attempt counter anyway.
MAX_TRACKED_SOURCES = 2048


class LoginAttempts:
    """Per-source failure counter with a sliding window. Refuses; never sleeps."""

    def __init__(self, *, now: float | None = None) -> None:
        self._fails: OrderedDict[str, list[float]] = OrderedDict()
        self._seeded_now = now

    def _clock(self) -> float:
        return self._seeded_now if self._seeded_now is not None else time.monotonic()

    def _prune(self, source: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(source, []) if now - t < WINDOW_SECONDS]
        if recent:
            self._fails[source] = recent
            self._fails.move_to_end(source)
        else:
            self._fails.pop(source, None)
        return recent

    def is_refused(self, source: str, *, now: float | None = None) -> bool:
        """True when *source* has failed too often inside the window."""
        return len(self._prune(source, now if now is not None else self._clock())) >= MAX_FAILURES

    def record_failure(self, source: str, *, now: float | None = None) -> int:
        """Count one failed sign-in. Returns the failures now inside the window."""
        t = now if now is not None else self._clock()
        recent = self._prune(source, t)
        recent.append(t)
        self._fails[source] = recent
        self._fails.move_to_end(source)
        while len(self._fails) > MAX_TRACKED_SOURCES:
            evicted, _ = self._fails.popitem(last=False)
            log.control_plane.info(
                "[control_plane] login_guard: evicted the least recently seen "
                "source — the tracking map is bounded on purpose",
                extra={"_fields": {"evicted": evicted, "cap": MAX_TRACKED_SOURCES}},
            )
        return len(recent)

    def record_success(self, source: str) -> None:
        """Forget a source's failures. A person who gets it right on the ninth
        attempt must not be one typo away from a lockout an hour later."""
        self._fails.pop(source, None)

    def tracked(self) -> int:
        """How many sources are being remembered — for the health surface."""
        return len(self._fails)
