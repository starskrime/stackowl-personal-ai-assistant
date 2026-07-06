"""Task 4 — /owl funnels every mutation through ONE owl_build.execute call, no
matter whether the caller used flags or free text (kills add-vs-create drift)."""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.owls_command import OwlCommand
from stackowl.commands.owls_helpers import parse_owl_build_flags
from stackowl.tools.base import ToolResult


class _State:
    session_id = "s1"
    trace_id = "t1"
    channel = "cli"
    reply_target = None


def test_parse_flags_freetext_maps_to_specialty() -> None:
    assert parse_owl_build_flags("a research owl that reads arxiv") == {
        "specialty": "a research owl that reads arxiv"
    }


def test_parse_flags_structured() -> None:
    kwargs = parse_owl_build_flags(
        '--name Sage --preset researcher --specialty "reads arxiv" '
        '--schedule "every 2h" --boundaries "no raw urls" '
        "--evolution_strategy conservative"
    )
    assert kwargs == {
        "name": "Sage", "preset": "researcher", "specialty": "reads arxiv",
        "schedule": "every 2h", "boundaries": "no raw urls",
        "evolution_strategy": "conservative",
    }


def test_parse_flags_explicit_tools_comma_list() -> None:
    kwargs = parse_owl_build_flags("--name S --explicit_tools read_file,memory")
    assert kwargs["explicit_tools"] == ["read_file", "memory"]


@pytest.mark.asyncio
async def test_owl_create_freetext_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Created owl 'x'.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    out = await OwlCommand().handle("create a research owl that reads arxiv", _State())
    assert out == "Created owl 'x'."
    assert seen == {"action": "create", "specialty": "a research owl that reads arxiv"}


@pytest.mark.asyncio
async def test_owl_pause_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Paused x.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    out = await OwlCommand().handle("pause Sage", _State())
    assert out == "Paused x."
    assert seen == {"action": "pause", "name": "Sage"}


@pytest.mark.asyncio
async def test_owl_rename_routes_positional_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            seen.update(kw)
            return ToolResult(success=True, output="Renamed.", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    await OwlCommand().handle('rename Sage "Sage the Scholar"', _State())
    assert seen == {"action": "rename", "name": "Sage", "display_name": "Sage the Scholar"}


@pytest.mark.asyncio
async def test_owl_list_uses_inherited_registry_surface() -> None:
    # No registry wired → the inherited _list returns the honest no-registry note.
    out = await OwlCommand().handle("list", _State())
    assert "no owl registry" in out.lower()
