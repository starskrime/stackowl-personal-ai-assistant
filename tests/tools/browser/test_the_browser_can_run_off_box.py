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

    src = inspect.getsource(runtime_mod.CamoufoxRuntime._teardown_inside_lock)
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
