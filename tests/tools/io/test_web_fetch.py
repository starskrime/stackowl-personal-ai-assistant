"""F-32 — web_fetch gates its result on the HTTP status.

The navigation status was captured but never read: a 404/500 error page was
returned as ``success=True`` and even auto-staged into memory as a low-confidence
fact. Now a non-2xx (or unreachable status 0) response is ``success=False`` with
the status surfaced, and such a response is NOT staged. A 2xx response is
unchanged: ``success=True`` and staged as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.io import web_fetch
from stackowl.tools.io.web_fetch import WebFetchTool


class _RecordingBridge:
    def __init__(self) -> None:
        self.staged: list[Any] = []

    async def stage(self, fact: Any) -> None:
        self.staged.append(fact)


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    *,
    status: int,
    html: str,
) -> _RecordingBridge:
    """Wire a stub browser runtime + recording memory bridge, and short-circuit the
    real navigation to return ``(status, html)``. Services are reset on teardown."""
    bridge = _RecordingBridge()
    runtime = SimpleNamespace(settings=SimpleNamespace())
    services = StepServices(browser_runtime=runtime, memory_bridge=bridge)  # type: ignore[arg-type]
    token = set_services(services)

    def _reset() -> None:
        # The token may have been created in a different context (async test vs
        # sync finalizer); fall back to installing fresh default services.
        try:
            reset_services(token)
        except ValueError:
            set_services(StepServices())

    request.addfinalizer(_reset)

    async def _fake_retry(op: Any, rt: Any, *, op_name: str) -> tuple[int, str]:
        return status, html

    # THE SSRF GUARD RESOLVES DNS FOR REAL, and this test is not about DNS.
    #
    # `_wire` short-circuits navigation, so the test reads as hermetic — and it was
    # not. `web_fetch` runs the egress guard BEFORE navigating, and
    # `SsrfGuard.__init__` binds `_default_resolve` (a live `socket.getaddrinfo`) at
    # IMPORT time into the module-level `_SSRF_GUARD`. So every run of these five
    # tests did a real lookup of `example.com`.
    #
    # MEASURED 2026-09-07: they passed alone and ALL FIVE failed in the full suite,
    # with "host 'example.com' did not resolve" — a transient failure on a box
    # 38 minutes into a suite run beside the live platform. `example.com` resolves
    # fine seconds later, so nothing was wrong with the code under test; a unit test
    # was simply reporting the network. That is a red suite pointing at the wrong
    # subsystem, which is expensive precisely when the suite is being trusted.
    #
    # The guard's POLICY still runs — a public address is fed in and must be allowed,
    # so the guard path is still exercised. Only the lookup is stubbed, and the guard
    # keeps its own dedicated coverage in tests/test_e0_s2_ssrf_guard.py.
    monkeypatch.setattr(
        web_fetch._SSRF_GUARD, "_resolve", lambda host: ["93.184.216.34"],
    )
    monkeypatch.setattr("stackowl.tools.io.web_fetch.with_browser_retry", _fake_retry)
    monkeypatch.setattr(
        "stackowl.tools.io.web_fetch.extract_markdown",
        lambda html, **kw: "Extracted page content.",
    )
    return bridge


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500, 403, 0])
async def test_non_2xx_is_failure_and_not_staged(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, status: int
) -> None:
    bridge = _wire(monkeypatch, request, status=status, html="<html></html>")

    result = await WebFetchTool().execute(url="https://example.com/missing")

    assert result.success is False
    # the status (or an unreachable marker) is surfaced in the error
    assert str(status) in (result.error or "") or "unreachable" in (result.error or "")
    # a failed fetch is NOT auto-staged as a memory fact
    assert bridge.staged == []


@pytest.mark.asyncio
async def test_2xx_is_success_and_stages_nothing(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """A successful fetch must NOT write to memory (D08.1).

    web_fetch used to stage every page as a "low-confidence webpage fact". The
    store has no reader for those any more, so this test was inverted rather
    than deleted — it now guards that the write stays gone, which is the thing
    that can regress.
    """
    bridge = _wire(monkeypatch, request, status=200, html="<html></html>")

    result = await WebFetchTool().execute(url="https://example.com/page")

    assert result.success is True, result.error
    assert result.output
    assert bridge.staged == []
