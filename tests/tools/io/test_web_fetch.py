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
    runtime = SimpleNamespace(settings=SimpleNamespace(enable_memory_caching=True))
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
async def test_2xx_is_success_and_staged(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    bridge = _wire(monkeypatch, request, status=200, html="<html></html>")

    result = await WebFetchTool().execute(url="https://example.com/page")

    assert result.success is True, result.error
    assert result.output
    assert len(bridge.staged) == 1
