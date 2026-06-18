"""ToolManifest.capability_tag field + web_knowledge class tagging (W3.T12)."""

from __future__ import annotations

from stackowl.tools.base import ToolManifest


def test_capability_tag_defaults_none() -> None:
    m = ToolManifest(name="x", description="d", parameters={})
    assert m.capability_tag is None


def test_web_knowledge_tools_tagged() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    assert reg.get("browser_browse").manifest.capability_tag == "web_knowledge"
    assert reg.get("web_search").manifest.capability_tag == "web_knowledge"
    assert reg.get("web_fetch").manifest.capability_tag == "web_knowledge"
