"""side_effect_committed honesty for browser write/consequential tools.

A pre-execution refusal (runtime/session unavailable, arg-validation, missing
local resource) reached BEFORE the page action runs leaves nothing crossing the
side-effect boundary — it must report ``side_effect_committed=False`` so it does
not trip the honest give-up floor. A failure AFTER the page action was attempted
keeps the conservative default True.

These drive ONLY the pre-exec paths (no real browser needed): with no browser
runtime wired, every write/consequential tool short-circuits at
``_services_or_unavailable``; arg-validation paths refuse even earlier.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.browser.browse import BrowserBrowseTool
from stackowl.tools.browser.tools import (
    BrowserClickTool,
    BrowserCookiesClearTool,
    BrowserCookiesSetTool,
    BrowserDownloadTool,
    BrowserEvalJsTool,
    BrowserScrollTool,
    BrowserTypeTool,
    BrowserUploadTool,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def no_browser_services():  # noqa: ANN201
    """Wire services with NO browser runtime/sessions so every tool short-circuits
    at the pre-exec 'Browser runtime not initialized' refusal."""
    token = set_services(StepServices(browser_runtime=None, browser_sessions=None))
    try:
        yield
    finally:
        reset_services(token)


# Write/consequential tools whose services-unavailable refusal is pure pre-exec.
_WRITE_TOOLS = [
    BrowserClickTool,
    BrowserTypeTool,
    BrowserScrollTool,
    BrowserEvalJsTool,  # consequential
    BrowserCookiesSetTool,
    BrowserCookiesClearTool,
]


@pytest.mark.parametrize("tool_cls", _WRITE_TOOLS)
async def test_runtime_unavailable_is_not_effectful(tool_cls, no_browser_services) -> None:  # noqa: ANN001
    result = await tool_cls().execute(session_id="s", selector="x", text="y", script="1", ref="e1")
    assert result.success is False
    assert "not initialized" in (result.error or "").lower()
    assert result.side_effect_committed is False  # never reached the page action


async def test_click_missing_args_is_not_effectful(no_browser_services) -> None:  # noqa: ANN001
    result = await BrowserClickTool().execute(session_id="s")  # no ref, no selector_or_text
    assert result.success is False
    assert result.side_effect_committed is False


async def test_upload_missing_local_file_is_not_effectful(no_browser_services) -> None:  # noqa: ANN001
    """The local file-existence check runs BEFORE any page action — pre-exec."""
    result = await BrowserUploadTool().execute(
        session_id="s", selector="input", file_path="/nonexistent/definitely-not-here.bin"
    )
    assert result.success is False
    assert "not found" in (result.error or "").lower()
    assert result.side_effect_committed is False


async def test_download_runtime_unavailable_is_not_effectful(no_browser_services) -> None:  # noqa: ANN001
    result = await BrowserDownloadTool().execute(session_id="s", trigger_selector="a")
    assert result.success is False
    assert result.side_effect_committed is False


async def test_browse_pre_loop_refusal_is_not_effectful(no_browser_services) -> None:  # noqa: ANN001
    """browser_browse refuses before its loop when the runtime is not initialized."""
    result = await BrowserBrowseTool().execute(task="do something")
    assert result.success is False
    assert result.side_effect_committed is False


async def test_browse_missing_task_is_not_effectful() -> None:
    result = await BrowserBrowseTool().execute(task="")
    assert result.success is False
    assert result.side_effect_committed is False
