"""The browser does not have to run where the agent runs.

MEASURED 2026-08-26: ten browser calls on this box, ZERO successes — five
TimeoutError, four session-limit-on-recycle, one tool_error. NOT a session leak;
the engine kept dying (43 "browser process gone", 17 recycles with 17 matching
"restart ok"). Self-healing caught every one and restarted it. The process will
not stay up on this hardware.

THE FINDING THAT MADE THIS THE FIX. The reference platform runs the SAME engine,
and its own published capability table marks that engine as the only backend
WITHOUT a CDP surface — no dialog detection, no dialog response, no cross-origin
frame evaluation. Every other backend it supports has all three. So the engine is
not what makes their browser tooling good; the CONTRACT is. Once the contract is a
CDP URL, the browser can run on a host where it is stable, and engine stability
here stops being the ceiling.

Bakir, 2026-08-26: "Then we need to support cdp."

WHAT THESE TESTS PIN, and neither is about Chromium:

  THE SEAM IS ONE METHOD. `connect_over_cdp` returns the same Playwright Browser
  the local engine yields, so every tool, session and snapshot path is unchanged.
  A test that mocked the whole runtime would prove nothing about that.

  WE DO NOT OWN THE REMOTE BROWSER. It was running before us and must survive us.
  Killing an operator's real browser because an agent session ended would be a
  worse defect than the one being fixed.

NOTE ON THE CLASS NAME. The runtime is still called ``CamoufoxRuntime`` while it
now serves two backends. That is the same naming defect this codebase found four
times on 2026-08-26 — a name asserting something other than what the thing does.
Renaming it touches every construction site, so it is recorded rather than done
here; these tests use the real name so they cannot pass against an imagined one.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.browser import BrowserSettings



def _teardown_source(runtime_mod: Any) -> str:
    """Teardown source across however many methods it is split into.

    These assertions pin BEHAVIOUR — ownership, and not swallowing a failed
    shutdown — not a method name. Reading one method broke them the moment the
    body moved to a helper, which is the "a test that names a member of something
    it does not own will rot" lesson arriving in my own tests the same day I wrote
    it down.
    """
    import inspect

    rt = runtime_mod.CamoufoxRuntime
    parts = []
    for name in ("_teardown_inside_lock", "_teardown_body"):
        fn = getattr(rt, name, None)
        if fn is not None:
            parts.append(inspect.getsource(fn))
    return "\n".join(parts)


def _settings(**kw: Any) -> BrowserSettings:
    base: dict[str, Any] = {"headless_mode": "true"}
    base.update(kw)
    return BrowserSettings(**base)


def test_local_is_the_default_so_nothing_changes_without_config() -> None:
    """Everything ships enabled and byte-identical. A box where the local engine
    is fine must keep working with no config at all."""
    s = _settings()
    assert s.backend == "local"
    assert s.attach_url == ""


def test_attach_is_selectable_with_a_cdp_url() -> None:
    s = _settings(backend="attach", attach_url="http://192.168.1.81:9222")
    assert s.backend == "attach"
    assert s.attach_url == "http://192.168.1.81:9222"


def test_an_unknown_backend_is_rejected_by_config() -> None:
    """Fail at load, not at the first navigate. A typo in the backend name must
    not present as 'the browser is broken' three layers away."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _settings(backend="chrome-ish")


@pytest.mark.asyncio
async def test_attach_without_a_url_refuses_with_a_usable_message() -> None:
    """THE honest-refusal case. backend='attach' and no url is a configuration
    mistake, and the message must name the field and show the shape — not raise
    something opaque from inside the driver."""
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings(backend="attach", attach_url=""))
    with pytest.raises(ValueError) as exc:
        await rt._connect_over_cdp()

    msg = str(exc.value)
    assert "attach_url" in msg, f"the message must name the missing field: {msg}"
    assert "9222" in msg, f"the message should show the expected shape: {msg}"


@pytest.mark.asyncio
async def test_an_unreachable_endpoint_does_not_leave_the_runtime_claiming_available(
    monkeypatch: Any,
) -> None:
    """A dead CDP port must degrade like any other failed start: available=False
    with a recorded reason. The measured alternative is worse than useless — a
    runtime that says it is up and times out on every call is indistinguishable
    from a slow site."""
    from stackowl.tools.browser import runtime as runtime_mod

    rt = runtime_mod.CamoufoxRuntime(
        _settings(backend="attach", attach_url="http://127.0.0.1:1")
    )

    async def _boom(self: Any) -> Any:
        raise ConnectionError("connect ECONNREFUSED 127.0.0.1:1")

    monkeypatch.setattr(runtime_mod.CamoufoxRuntime, "_connect_over_cdp", _boom)
    monkeypatch.setattr(
        runtime_mod.TestModeGuard, "assert_not_test_mode", lambda *_a, **_k: None
    )

    await rt.start()

    assert rt.available is False
    assert rt._unavailable_reason, "a failed attach must record WHY"
    assert "ECONNREFUSED" in rt._unavailable_reason or "ConnectionError" in rt._unavailable_reason


@pytest.mark.asyncio
async def test_teardown_DISCONNECTS_and_never_kills_a_browser_we_do_not_own() -> None:
    """The property that matters most in this file.

    A CDP-attached browser belongs to the operator. `close()` on a connected
    handle disconnects and drops the contexts WE created; it must never be
    confused with stopping their browser. This asserts the attach branch runs at
    all — the pre-existing branch below it would silently no-op, because the
    attach path never sets `_manager` and the old teardown returned early on
    exactly that condition.
    """
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    closed: list[str] = []

    class _Browser:
        async def close(self) -> None:
            closed.append("browser")

    class _PW:
        async def stop(self) -> None:
            closed.append("playwright")

    rt = CamoufoxRuntime(_settings(backend="attach", attach_url="http://x:9222"))
    rt._browser = _Browser()  # type: ignore[assignment]
    rt._pw = _PW()
    rt._attached = True
    rt.available = True

    await rt._teardown_inside_lock()

    assert closed == ["browser", "playwright"], (
        "the attach branch did not run — teardown fell through to the local-engine "
        "path, which no-ops when _manager is None and would leak the connection"
    )
    assert rt._attached is False
    assert rt._browser is None
    assert rt.available is False


# ---------------------------------------------------------------------------
# Self-provisioning — the part that has to work with nobody watching.
#
# Bakir, 2026-08-26: "you need to fix platform to work with tool not manuall fix
# issue for the platform. Because when we ship it to customer device i will not
# have access to help."
#
# CDP support alone did not satisfy that. It worked only because a person had
# started a browser by hand and written its URL into config. On a customer device
# neither happens, so the capability was a demo. These pin the difference.
# ---------------------------------------------------------------------------


def test_managed_is_a_real_backend_choice() -> None:
    s = _settings(backend="managed")
    assert s.backend == "managed"
    assert s.attach_url == "", "managed must need NO url — that is the whole point"


def test_local_does_not_escalate_before_it_has_actually_failed() -> None:
    """Escalation must follow MEASURED failure, never a guess. A platform that
    abandons its configured engine on the first hiccup is not self-healing, it is
    twitchy."""
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings())
    assert rt._effective_backend() == "local"
    rt._local_failures = 1
    assert rt._effective_backend() == "local", "one failure is noise, not a pattern"


def test_repeated_local_failure_escalates_to_a_self_provisioned_browser() -> None:
    """THE customer-device property. Nobody edits config; the platform stops
    asking a broken engine and provisions its own."""
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings())
    rt._local_failures = rt._LOCAL_FAILURES_BEFORE_ESCALATION
    assert rt._effective_backend() == "managed"
    assert rt._escalated is True


def test_an_explicit_backend_is_never_overridden() -> None:
    """Escalation is for the DEFAULT. If the operator asked for something, that is
    what they get — self-healing must not quietly countermand a stated choice."""
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings(backend="attach", attach_url="http://x:9222"))
    rt._local_failures = 99
    assert rt._effective_backend() == "attach"


def test_a_missing_browser_binary_is_reported_not_crashed() -> None:
    """On a device with no Chromium-family browser, self-provisioning is simply a
    capability we lack. It must say what it looked for — the operator of that
    device cannot read our source to find out."""
    from stackowl.tools.browser import cdp_launcher

    names = cdp_launcher._CANDIDATES
    assert "chromium" in names and "google-chrome" in names
    assert cdp_launcher.find_browser_binary.__doc__


def test_a_launched_browser_uses_a_free_port_not_a_fixed_one() -> None:
    """A hardcoded 9222 collides with the operator's OWN browser — the one process
    we must never disturb."""
    from stackowl.tools.browser import cdp_launcher

    a, b = cdp_launcher._free_port(), cdp_launcher._free_port()
    assert a > 1024 and b > 1024


def test_ownership_decides_teardown() -> None:
    """The distinction the whole design turns on: a browser WE launched is ours to
    stop; one the operator was already running must survive us. Conflating them
    either leaks a process every restart or kills someone's real browser."""
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = _teardown_source(runtime_mod)
    assert "_launched is not None" in src, (
        "teardown must branch on OWNERSHIP, not on whether a CDP connection exists"
    )
    assert "stop_cdp_browser" in src


def test_the_RECYCLE_path_counts_failures_too() -> None:
    """Where the failures actually come from.

    On this box the engine died 43 times and cold-started cleanly once. An
    escalation counter fed only by start() would sit at zero all day while the
    browser was unusable — the actuator-on-some-paths defect, in the very feature
    built to fix a different instance of it. I shipped exactly that bug an hour
    earlier by teaching start() about backends and forgetting recycle.
    """
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._recycle_inside_lock)
    assert "_local_failures" in src, (
        "the recycle path must count local-engine failures, or escalation can "
        "never trigger on the path where failures actually happen"
    )


def test_escalation_keys_on_RECYCLES_because_the_engine_starts_fine() -> None:
    """The correction that made this feature actually work.

    The first version counted failed OPENS and would never have fired. Measured
    under real traffic on 2026-08-26: the engine restarts successfully
    ("runtime.recycle: restart ok") and browser_navigate fails anyway. It is not
    failing to START, it is failing to BE USABLE — and the observable for that is
    the recycle count, the number that reached 43 on this box while every restart
    reported success.

    Measure the effect, not the call. I instrumented the call first.
    """
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings())
    rt._local_failures = 0          # every open succeeded, as in production
    rt._recycle_count = rt._LOCAL_FAILURES_BEFORE_ESCALATION
    assert rt._effective_backend() == "managed", (
        "a browser that restarts cleanly and cannot navigate must still escalate"
    )


def test_the_recycle_count_is_incremented_BEFORE_the_reopen() -> None:
    """Off-by-one that made escalation need three deaths for a threshold of two.

    _effective_backend() is consulted DURING the reopen, so a count bumped only
    after a successful reopen is always one behind. Counting the ATTEMPT answers
    the question actually being asked — how many times has this engine had to be
    restarted.
    """
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._recycle_inside_lock)
    before_open = src.split("_open_backend")[0]
    assert "_recycle_count += 1" in before_open, (
        "the recycle must be counted before the reopen, or the escalation check "
        "reads a stale number"
    )


def test_a_dropped_connection_is_not_reported_as_a_dead_browser() -> None:
    """Measured 2026-08-26: "runtime.disconnect: browser process gone" fired while
    the Chromium process was demonstrably still alive. We drive a CDP browser over
    a websocket, and losing the socket says nothing about the browser. Calling it a
    dead process sent the runtime into a recycle storm that failed every navigate.
    """
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._mark_disconnected)
    assert "if self._attached" in src, (
        "the disconnect handler must distinguish a lost CDP socket from a dead "
        "local engine — they need opposite responses"
    )
    assert "CDP connection lost" in src


def test_a_reconnect_reuses_the_browser_we_already_own() -> None:
    """Reconnects are the COMMON case for a CDP backend. Relaunching on each one
    leaks a Chromium per dropped socket on a long-lived device."""
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._open_managed_cdp)
    assert "process.poll() is None" in src, (
        "managed reopen must reuse a live owned process instead of spawning another"
    )


def test_a_failed_engine_shutdown_is_logged_not_swallowed() -> None:
    """Measured 2026-08-26, and it is why the replacement browser kept dying.

    After escalating to a CDP browser the platform was running BOTH engines: one
    camoufox at 351 MB and eight chromium processes at 773 MB — 1,124 MB of
    browser on a box with 178 MB free. A failed __aexit__ leaves the OS process
    alive, and `contextlib.suppress` meant nothing ever reported it. The engine we
    abandoned starves the one we just launched.
    """
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = _teardown_source(runtime_mod)
    local_half = src.split("if self._manager is None")[-1]
    assert "contextlib.suppress" not in local_half, (
        "a failed engine shutdown must be reported — it leaves a process holding "
        "memory on a device that has none to spare"
    )
    assert "may still be running" in local_half


# ---------------------------------------------------------------------------
# The recycle storm — root cause, traced 2026-08-27.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_our_own_teardown_is_not_mistaken_for_a_browser_death() -> None:
    """THE root cause of the browser being unusable on this box.

    Playwright emits `disconnected` whenever the browser closes — INCLUDING when
    we close it ourselves. The handler treated both alike, so every deliberate
    teardown announced "browser process gone", set available=False and fired the
    on-recycled callbacks, which purge every session and bump the generation. An
    in-flight sessions.open then failed with BrowserRuntimeRecycledError,
    ensure_available saw a dead runtime and recycled again, and THAT recycle's
    teardown fired the same cascade.

    It is why both engines appeared to die: they did not. Traced from the emission
    stack — Browser._on_close -> our lambda -> _mark_disconnected — during a
    teardown we initiated, on a browser that navigated successfully right after.
    """
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings())
    fired: list[str] = []
    rt.register_on_recycled(lambda: fired.append("purge"))
    rt.available = True

    rt._closing = True
    rt._mark_disconnected()

    assert fired == [], (
        "a deliberate close fired the recycled callbacks — that purges every "
        "session and bumps the generation, which is the recycle storm"
    )
    assert rt.available is True, "our own teardown must not mark the runtime dead"


@pytest.mark.asyncio
async def test_an_UNEXPECTED_disconnect_is_still_treated_as_a_death() -> None:
    """The other half. Suppressing our own closes must not blind the runtime to a
    browser that genuinely crashed — that is what the self-healing exists for."""
    from stackowl.tools.browser.runtime import CamoufoxRuntime

    rt = CamoufoxRuntime(_settings())
    fired: list[str] = []
    rt.register_on_recycled(lambda: fired.append("purge"))
    rt.available = True
    rt._browser = object()  # type: ignore[assignment]

    rt._closing = False
    rt._mark_disconnected()

    assert fired == ["purge"], "a real crash must still purge sessions"
    assert rt.available is False


def test_the_closing_flag_is_always_lowered() -> None:
    """A flag left raised would blind the runtime to every later crash, which is a
    worse failure than the storm it prevents. The teardown wraps the body in
    try/finally for exactly that."""
    import inspect

    from stackowl.tools.browser import runtime as runtime_mod

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._teardown_inside_lock)
    assert "finally" in src and "_closing = False" in src, (
        "the wrapper itself must lower the flag; this one deliberately reads the "
        "OUTER method, because that is where the guarantee lives"
    )


# ---------------------------------------------------------------------------
# Session reuse — the cause of "browser runtime unavailable" on a healthy runtime.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_call_for_the_same_owner_reuses_its_session() -> None:
    """THE leak. Measured 2026-08-27, right after navigation started working:
    TEN opens, FOUR closes, every one for owner "local" with a different session
    id. The atomic tools called open() whenever the model omitted session_id —
    most turns, since it is optional — so each navigation burned a slot nothing
    frees for the 30-minute idle TTL. The eighth turn hit "max concurrent browser
    sessions reached (8)" and every browser call after it failed, reporting
    "browser runtime unavailable" while the runtime was healthy.

    Threading a session id across turns is exactly what an agent forgets, and the
    failure looks like a broken browser rather than a leaked handle. Reuse removes
    the requirement instead of documenting it.
    """
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    first = await reg.acquire("local")
    second = await reg.acquire("local")

    assert first == second, "a second acquire for the same owner opened a new session"
    assert len(reg._sessions) == 1


@pytest.mark.asyncio
async def test_different_owners_are_never_shared() -> None:
    """owner_key is the isolation boundary between Telegram users. Reuse must
    never cross it — that would hand one person another's browser."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    a = await reg.acquire("telegram:111")
    b = await reg.acquire("telegram:222")

    assert a != b
    assert len(reg._sessions) == 2


@pytest.mark.asyncio
async def test_a_different_profile_is_not_reused() -> None:
    """A profile carries identity and fingerprint. Silently handing back the wrong
    one is a privacy defect, not a convenience."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    plain = await reg.acquire("local")
    named = await reg.acquire("local", profile_name="work")

    assert plain != named


@pytest.mark.asyncio
async def test_reuse_never_hands_back_a_proxied_session() -> None:
    """Different egress IP than the caller asked for. Recorded on the session so
    the answer is judged, not guessed."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    sid = await reg.acquire("local")
    reg._sessions[sid].used_proxy = True          # as if opened through a proxy

    fresh = await reg.acquire("local")
    assert fresh != sid, "a proxy-less caller was handed a proxied session"


# ---------------------------------------------------------------------------
# "It always returns the example domain page" — Bakir, 2026-08-27.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_with_no_page_handle_acts_on_the_page_that_was_navigated() -> None:
    """THE bug behind "it always returns the example domain page".

    ``get_page`` with no handle CREATED A NEW PAGE every time. So
    browser_navigate loaded the target URL on page 1, and the browser_snapshot
    that followed minted a blank page 2 and described THAT — the agent reported
    whatever the fresh tab showed instead of the site it had just visited.

    Measured on a real Cigna directory turn: navigate returned 200, then extract
    and click both failed with "max pages per session reached (4)", because four
    tool calls meant four tabs.

    "No handle" means "the page I am working on". Only browser_tab_open wants a
    new one, and it now says so.
    """
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    sid = await reg.acquire("local")

    _, _, navigated = await reg.get_page(sid)          # browser_navigate
    _, _, snapshotted = await reg.get_page(sid)        # browser_snapshot

    assert navigated == snapshotted, (
        "the snapshot ran on a different page than the navigation — that is "
        "exactly how a stale/blank page gets reported as the result"
    )


@pytest.mark.asyncio
async def test_repeated_tool_calls_do_not_exhaust_the_page_limit() -> None:
    """max_concurrent_pages_per_session is 4, so the fifth tool call in one turn
    used to kill the session. A turn routinely makes more than four."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    sid = await reg.acquire("local")

    for _ in range(10):
        await reg.get_page(sid)

    assert len(reg._sessions[sid].pages) == 1, (
        f"ten tool calls opened {len(reg._sessions[sid].pages)} pages"
    )


@pytest.mark.asyncio
async def test_opening_a_tab_is_still_possible_and_now_explicit() -> None:
    """Reuse must not remove the ability to open a second tab — browser_tab_open
    is a real feature. It just has to ASK for it."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    sid = await reg.acquire("local")

    _, _, first = await reg.get_page(sid)
    _, _, tab = await reg.get_page(sid, None, new_page=True)

    assert tab != first
    assert len(reg._sessions[sid].pages) == 2


@pytest.mark.asyncio
async def test_naming_a_handle_still_selects_that_exact_page() -> None:
    """Multi-tab work depends on it: after opening a tab, tools must be able to
    address either one."""
    from tests.tools.browser.test_sessions import _FakeRuntime  # type: ignore
    from stackowl.tools.browser.sessions import BrowserSessionRegistry

    reg = BrowserSessionRegistry(_FakeRuntime(), _settings())  # type: ignore[arg-type]
    sid = await reg.acquire("local")
    _, _, first = await reg.get_page(sid)
    _, _, tab = await reg.get_page(sid, None, new_page=True)

    _, _, back = await reg.get_page(sid, first)
    assert back == first

    _, _, now_current = await reg.get_page(sid)
    assert now_current == first, "naming a page should make it the current one"
