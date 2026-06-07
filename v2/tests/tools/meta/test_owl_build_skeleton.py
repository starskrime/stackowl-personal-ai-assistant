"""Skeleton tests for owl_build (Phase-2 A): manifest shape, registration, child-exclude."""

from __future__ import annotations

from stackowl.pipeline.steps.execute import _CHILD_EXCLUDED_TOOLS
from stackowl.tools.meta.owl_build import OwlBuildTool
from stackowl.tools.registry import ToolRegistry


def test_owl_build_is_consequential_and_isolated() -> None:
    m = OwlBuildTool().manifest
    assert m.name == "owl_build"
    assert m.action_severity == "consequential"
    assert m.toolset_group  # has its own isolated group


def test_owl_build_registered_in_defaults() -> None:
    reg = ToolRegistry.with_defaults()
    assert reg.get("owl_build") is not None


def test_owl_build_is_child_excluded() -> None:
    assert "owl_build" in _CHILD_EXCLUDED_TOOLS
