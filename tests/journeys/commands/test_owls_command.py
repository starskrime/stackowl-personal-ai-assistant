"""Dispatch tests — /owl create/edit funnel through OwlBuildTool.execute (the
ONE owl mutation engine). /owls' old add/edit direct-registry path and its
add-vs-create divergence are what /owl replaces (source deleted in Task 7);
these assert the /owl replacement routes correctly instead.

`test_owls_create_freetext_empty_raises_parse_error` still drives OwlsCommand
directly — that source is untouched by this task (deleted in Task 7), so its
own free-text validation is still live and worth covering until then.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.commands.owls_command import OwlCommand, OwlsCommand
from stackowl.exceptions import CommandParseError
from tests._story_6_7_helpers import make_state, no_test_mode_guard  # noqa: F401


class _MinimalOwlRegistry:
    """Minimal registry that accepts add/edit without a real DB."""

    def __init__(self) -> None:
        self._owls: dict[str, object] = {}

    def register(self, manifest: object) -> None:
        self._owls[manifest.name] = manifest  # type: ignore[union-attr]

    def replace(self, manifest: object) -> None:
        self._owls[manifest.name] = manifest  # type: ignore[union-attr]

    def get(self, name: str) -> object:
        from stackowl.exceptions import OwlNotFoundError
        if name not in self._owls:
            raise OwlNotFoundError(name)
        return self._owls[name]

    def list(self) -> list:
        return list(self._owls.values())

    async def health_check(self) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(status="healthy", message=None)

    def deregister(self, name: str) -> None:
        self._owls.pop(name, None)


class _StubResult:
    success = True
    output = "✓ stubbed"
    error = None


async def test_owl_create_with_flags_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/owl create --name ... --tier ...` funnels through OwlBuildTool.execute
    with action="create" and the parsed flags — replaces /owls add's direct
    registry-manipulation path (add's --role/--tier grammar is now owl_build's)."""
    seen: dict[str, Any] = {}

    class _StubOwlBuildTool:
        async def execute(self, **kwargs: object) -> _StubResult:
            seen.update(kwargs)
            return _StubResult()

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _StubOwlBuildTool)
    result = await OwlCommand().handle("create --name testowl --tier standard", make_state())

    assert seen == {"action": "create", "name": "testowl", "model_tier": "standard"}
    assert "✓ stubbed" in result


async def test_owl_edit_routes_to_owl_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/owl edit <name> --tier ...` funnels through OwlBuildTool.execute with
    action="edit" — replaces /owls edit's direct registry-manipulation path."""
    seen: dict[str, Any] = {}

    class _StubOwlBuildTool:
        async def execute(self, **kwargs: object) -> _StubResult:
            seen.update(kwargs)
            return _StubResult()

    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _StubOwlBuildTool)
    result = await OwlCommand().handle("edit editowl --tier powerful", make_state())

    assert seen == {"action": "edit", "name": "editowl", "model_tier": "powerful"}
    assert "✓ stubbed" in result


async def test_owl_create_freetext_reaches_owl_build_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/owl create <free text>` reaches OwlBuildTool.execute(action="create", ...)
    with the free-text argument passed through UNPARSED — no shlex tokenisation, no
    --flag stripping — proving the grammar-bypass regression this fixes."""
    calls: list[dict] = []

    class _StubOwlBuildTool:
        async def execute(self, **kwargs: object) -> _StubResult:
            calls.append(kwargs)
            return _StubResult()

    # Patched at the ORIGIN module: owls_command imports OwlBuildTool lazily
    # inside the method, so a fresh `from ... import OwlBuildTool` at call time
    # resolves via stackowl.tools.meta.owl_build's current attribute.
    monkeypatch.setattr("stackowl.tools.meta.owl_build.OwlBuildTool", _StubOwlBuildTool)
    sentence = "a researcher that reads arxiv daily and summarizes transformer papers"
    result = await OwlCommand().handle(f"create {sentence}", make_state())

    assert len(calls) == 1
    assert calls[0]["action"] == "create"
    assert calls[0]["specialty"] == sentence
    assert "✓ stubbed" in result


async def test_owls_create_freetext_empty_raises_parse_error() -> None:
    """`/owls create` with empty/whitespace-only text raises CommandParseError,
    mirroring parse_add_args'/parse_edit_args' missing-required-arg convention."""
    cmd = OwlsCommand(owl_registry=_MinimalOwlRegistry())
    with pytest.raises(CommandParseError):
        await cmd._create_freetext("   ", make_state())
