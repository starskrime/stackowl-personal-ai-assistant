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
