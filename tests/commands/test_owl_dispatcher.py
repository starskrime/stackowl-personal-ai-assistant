"""Task 4 — /owl funnels every mutation through ONE owl_build.execute call, no
matter whether the caller used flags or free text (kills add-vs-create drift)."""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.owls_command import OwlCommand
from stackowl.commands.owls_helpers import parse_owl_build_flags
from stackowl.commands.response import CommandResponse
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
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
async def test_owl_build_populates_services_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: OwlBuildTool reads registry/db via get_services(), which is
    # ONLY populated by the LLM pipeline backend (asyncio_backend/
    # langgraph_backend) — slash-command dispatch bypasses that entirely, so
    # any OwlBuildTool action reading get_services().db_pool (e.g. pause/
    # resume's scheduling check) always saw an empty StepServices() and failed
    # closed with "owl scheduling unavailable" even when the command itself
    # was constructed with real registry/db deps.
    from stackowl.pipeline.services import get_services

    seen: dict[str, Any] = {}

    class _FakeTool:
        async def execute(self, **kw: Any) -> ToolResult:
            svc = get_services()
            seen["registry_wired"] = svc.owl_registry is not None
            seen["db_wired"] = svc.db_pool is not None
            return ToolResult(success=True, output="ok", duration_ms=1.0)

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _FakeTool)
    reg = OwlRegistry.with_default_secretary()
    db = object()  # any non-None sentinel — only identity/None-ness is asserted
    out = await OwlCommand(owl_registry=reg, db=db).handle("pause secretary", _State())
    assert out == "ok"
    assert seen == {"registry_wired": True, "db_wired": True}


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


@pytest.mark.asyncio
async def test_owl_menu_unknown_name_errors_cleanly() -> None:
    reg = OwlRegistry.with_default_secretary()
    out = await OwlCommand(owl_registry=reg).handle("menu ghost", _State())
    assert isinstance(out, str)
    assert "✗" in out


@pytest.mark.asyncio
async def test_owl_menu_on_demand_owl_has_retire_but_no_pause_resume() -> None:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="Sage", role="researcher",
            system_prompt="You are Sage.", model_tier="fast",
        )
    )
    out = await OwlCommand(owl_registry=reg).handle("menu Sage", _State())
    assert isinstance(out, CommandResponse)
    labels = {a.label for a in out.actions}
    assert "Retire Sage" in labels
    assert not ({"Pause", "Resume"} & labels)
    retire = next(a for a in out.actions if a.label == "Retire Sage")
    assert retire.destructive is True
    assert retire.command == "/owl retire Sage"


@pytest.mark.asyncio
async def test_owl_list_actions_open_menu() -> None:
    reg = OwlRegistry.with_default_secretary()
    out = await OwlCommand(owl_registry=reg).handle("list", _State())
    assert isinstance(out, CommandResponse)
    assert any(a.command == "/owl menu secretary" for a in out.actions)


@pytest.mark.asyncio
async def test_owl_list_has_add_button_even_when_populated() -> None:
    reg = OwlRegistry.with_default_secretary()
    out = await OwlCommand(owl_registry=reg).handle("list", _State())
    assert isinstance(out, CommandResponse)
    add = next(a for a in out.actions if a.label == "+ Add owl")
    assert add.command == "/owl create"
    assert add.destructive is False


@pytest.mark.asyncio
async def test_owl_menu_has_set_tier_buttons_excluding_current() -> None:
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="Sage", role="researcher",
            system_prompt="You are Sage.", model_tier="fast",
        )
    )
    out = await OwlCommand(owl_registry=reg).handle("menu Sage", _State())
    assert isinstance(out, CommandResponse)
    labels = {a.label for a in out.actions}
    assert "Set tier: standard" in labels
    assert "Set tier: powerful" in labels
    assert "Set tier: local" in labels
    assert "Set tier: fast" not in labels
    set_standard = next(a for a in out.actions if a.label == "Set tier: standard")
    assert set_standard.command == "/owl edit Sage --tier standard"
    assert set_standard.destructive is False
