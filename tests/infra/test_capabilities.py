"""D05.3 — the capability registry: gating, flake suppression, fail-open.

The most important tests here are the ones that assert the registry REFUSES to
hide a tool: fail-open on an unknown name, and the grace window. Losing 25 tools
to a typo or a slow probe is the failure mode this item is supposed to end, not
introduce.
"""

from __future__ import annotations

import pytest

from stackowl.infra import capabilities


class _Resource:
    """Minimal HealableResource stand-in."""

    def __init__(self, ok=True, reason=None, remedy=None, raises=False):
        self.ok, self._reason, self._remedy, self._raises = ok, reason, remedy, raises
        self.recycled_cb = None

    @property
    def available(self):
        if self._raises:
            raise RuntimeError("probe exploded")
        return self.ok

    @property
    def unavailable_reason(self):
        return self._reason

    @property
    def remedy(self):
        return self._remedy

    def register_on_recycled(self, cb):
        self.recycled_cb = cb


@pytest.fixture(autouse=True)
def _clean():
    capabilities.clear()
    yield
    capabilities.clear()


def _fresh(name="browser"):
    """Register and drop the cached verdict, so each probe is observed."""
    capabilities.invalidate_cache(name)


# --------------------------------------------------------------------------- #
# Fail-open — every uncertain path must PRESENT the tool.
# --------------------------------------------------------------------------- #


def test_no_capability_declared_is_available():
    assert capabilities.resolve(None).ok
    assert capabilities.resolve("").ok


def test_an_unknown_capability_name_fails_OPEN():
    """A typo in requires_capability must present the tool, never hide it.

    Fail-closed would turn one misspelled string into a silently missing toolset
    — exactly the bug class this item exists to end.
    """
    assert capabilities.resolve("brwoser").ok


def test_an_unregistered_name_is_not_cached_as_available():
    """A resource may simply not be wired YET at this point in startup. Caching
    'unknown' would pin that verdict for the whole TTL, so a capability that
    registers a moment later would stay invisible for 30 seconds."""
    assert capabilities.resolve("browser").ok
    capabilities.register("browser", _Resource(ok=False, reason="down"))
    assert not capabilities.resolve("browser").ok


def test_a_raising_probe_within_grace_keeps_the_tool():
    r = _Resource(ok=True)
    capabilities.register("browser", r)
    assert capabilities.resolve("browser").ok       # establishes last-good
    r._raises = True
    _fresh()
    assert capabilities.resolve("browser").ok, "a raising probe must not strip tools"


# --------------------------------------------------------------------------- #
# The gate itself.
# --------------------------------------------------------------------------- #


def test_an_unavailable_resource_gates_with_reason_and_remedy():
    capabilities.register("browser", _Resource(
        ok=False, reason="missing libx11-xcb", remedy="sudo apt install libx11-xcb1",
    ))
    v = capabilities.resolve("browser")
    assert not v.ok
    assert v.reason == "missing libx11-xcb"
    assert v.remedy == "sudo apt install libx11-xcb1"


def test_a_resource_without_a_remedy_is_fine():
    """`remedy` is deliberately NOT on the HealableResource Protocol — all ten
    existing implementers predate it and must keep conforming."""
    class _NoRemedy:
        available = False
        unavailable_reason = "socket closed"
        def register_on_recycled(self, cb): pass

    capabilities.register("db", _NoRemedy())
    v = capabilities.resolve("db")
    assert not v.ok and v.reason == "socket closed" and v.remedy is None


# --------------------------------------------------------------------------- #
# Flake suppression — the borrowed scar tissue.
# --------------------------------------------------------------------------- #


def test_a_failure_within_the_grace_window_is_treated_as_a_flake():
    """One slow probe must not strip a toolset mid-session.

    This is the reference platform's incident #21658/#5304 in miniature: a
    `docker version` that timed out under load returned False once, and a
    delegated subagent then reported "Tool read_file does not exist".
    """
    r = _Resource(ok=True)
    capabilities.register("browser", r)
    assert capabilities.resolve("browser").ok      # last-good recorded

    r.ok = False
    _fresh()
    assert capabilities.resolve("browser").ok, "a transient failure must be suppressed"


def test_the_flake_is_not_cached_so_the_next_call_reprobes():
    """Serving last-good must NOT pin that verdict for the whole TTL — otherwise
    a genuine outage would be masked for 30s after every blip."""
    r = _Resource(ok=True)
    capabilities.register("browser", r)
    capabilities.resolve("browser")
    r.ok = False
    _fresh()
    capabilities.resolve("browser")               # suppressed, not cached
    r.ok = True
    assert capabilities.resolve("browser").ok     # re-probed, no invalidate needed


def test_a_failure_past_the_grace_window_is_honoured(monkeypatch):
    """A subsystem that really went down must stop advertising its tools."""
    monkeypatch.setattr(capabilities, "_FAILURE_GRACE_SECONDS", 0.0)
    r = _Resource(ok=True)
    capabilities.register("browser", r)
    capabilities.resolve("browser")
    r.ok, r._reason = False, "really down"
    _fresh()
    v = capabilities.resolve("browser")
    assert not v.ok and v.reason == "really down"


def test_a_failure_with_NO_prior_success_is_honoured_immediately():
    """The grace window keys on a RECENT SUCCESS. A capability that has never
    worked has nothing to fall back to and must gate at once."""
    capabilities.register("browser", _Resource(ok=False, reason="never started"))
    assert not capabilities.resolve("browser").ok


# --------------------------------------------------------------------------- #
# Invalidation wiring.
# --------------------------------------------------------------------------- #


def test_register_hooks_the_recycle_callback():
    r = _Resource()
    capabilities.register("browser", r)
    assert callable(r.recycled_cb), "recycle must invalidate without polling"


def test_a_recycle_invalidates_the_cached_verdict():
    r = _Resource(ok=False, reason="down")
    capabilities.register("browser", r)
    assert not capabilities.resolve("browser").ok
    r.ok = True
    r.recycled_cb()                                # the subsystem recycled
    assert capabilities.resolve("browser").ok


def test_subscribers_are_notified_and_one_raising_does_not_block_others():
    # Save/restore rather than relying on clear(): subscribers are WIRING, and
    # clear() deliberately keeps them (presented_tools subscribes once at import
    # and nothing re-subscribes). Without this the raising subscriber below would
    # leak into every later test in the process.
    saved = list(capabilities._subscribers)
    try:
        seen = []
        capabilities.subscribe_to_changes(
            lambda n: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        capabilities.subscribe_to_changes(seen.append)
        capabilities.invalidate_cache("browser")
        assert seen == ["browser"], "a bad subscriber must not swallow the others"
    finally:
        capabilities._subscribers[:] = saved


def test_registering_a_resource_drops_any_stale_verdict():
    capabilities.register("browser", _Resource(ok=False, reason="old"))
    assert not capabilities.resolve("browser").ok
    capabilities.register("browser", _Resource(ok=True))
    assert capabilities.resolve("browser").ok
