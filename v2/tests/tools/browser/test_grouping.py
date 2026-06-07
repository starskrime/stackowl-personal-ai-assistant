"""E2 substrate (FF-E2-2 / task #7) — every browser tool is in the 'browser'
toolset group, and the grouping refactor preserved each tool's exact severity.

The severity map is the authoritative pre-refactor snapshot — any drift (e.g. a
consequential tool silently becoming 'read' and skipping the consent gate) fails
this test loudly.
"""

from __future__ import annotations

from stackowl.tools.registry import ToolRegistry

# name -> action_severity, frozen from the pre-refactor snapshot.
_EXPECTED_SEVERITY = {
    "browser_back": "read",
    "browser_browse": "consequential",
    "browser_click": "write",
    "browser_close": "write",
    "browser_console": "read",
    "browser_cookies_clear": "write",
    "browser_cookies_get": "read",
    "browser_cookies_set": "write",
    "browser_dialog": "consequential",
    "browser_download": "consequential",
    "browser_eval_js": "consequential",
    "browser_extract": "read",
    "browser_get_images": "read",
    "browser_navigate": "read",
    "browser_press": "read",
    "browser_recall_url": "read",
    "browser_screenshot": "read",
    "browser_scroll": "write",
    "browser_snapshot": "read",
    "browser_tab_close": "write",
    "browser_tab_list": "read",
    "browser_tab_open": "read",
    "browser_type": "write",
    "browser_upload": "consequential",
    "browser_vision": "read",
    "browser_wait_for": "read",
}


def _browser_tools() -> dict[str, object]:
    reg = ToolRegistry.with_defaults()
    return {t.name: t for t in reg.all() if t.name.startswith("browser_")}


def test_every_browser_tool_is_grouped() -> None:
    tools = _browser_tools()
    # browser_vision is a media/vision composite (toolset_group="media") whose name
    # starts with "browser_"; it is intentionally in the "media" group.
    _MEDIA_CROSSOVERS = {"browser_vision"}
    ungrouped = [
        n for n, t in tools.items()
        if t.manifest.toolset_group != "browser" and n not in _MEDIA_CROSSOVERS  # type: ignore[attr-defined]
    ]
    assert ungrouped == [], f"browser tools missing toolset_group='browser': {ungrouped}"


def test_severities_preserved_exactly() -> None:
    tools = _browser_tools()
    actual = {n: t.manifest.action_severity for n, t in tools.items()}  # type: ignore[attr-defined]
    assert actual == _EXPECTED_SEVERITY


def test_full_catalog_count_unchanged() -> None:
    # Guards against accidentally dropping or duplicating a browser tool.
    assert len(_browser_tools()) == len(_EXPECTED_SEVERITY)
