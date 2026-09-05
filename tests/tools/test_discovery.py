"""D05.1 — tool auto-discovery, and the shared-dependency defect it would have shipped.

The parity test matters less than the SHARED-STORE test. Discovery finding 77
classes is easy; the failure mode that nearly shipped is subtler — every
shared-dependency constructor accepts ``store=None`` and quietly builds its own,
so auto-instantiating them registers five working tools and leaves ``undo_write``
unable to undo anything ``edit`` did. Green tests, broken behaviour.
"""

from __future__ import annotations

import pytest

from stackowl.tools._infra.discovery import (
    EXCLUDED_FROM_DISCOVERY,
    _defines_a_tool_subclass,
    discover_tool_classes,
    requires_explicit_wiring,
)
from stackowl.tools.base import Tool
from stackowl.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# The AST prefilter — its whole job is to NOT import 69 helper modules.
# --------------------------------------------------------------------------- #


def test_the_prefilter_accepts_a_module_defining_a_tool():
    assert _defines_a_tool_subclass("class ShellTool(Tool):\n    pass\n")


def test_the_prefilter_accepts_a_dotted_base():
    assert _defines_a_tool_subclass("class X(base.Tool):\n    pass\n")


def test_the_prefilter_rejects_a_helper_module():
    assert not _defines_a_tool_subclass("def helper():\n    return 1\n")


def test_the_prefilter_rejects_a_class_that_is_not_a_tool():
    assert not _defines_a_tool_subclass("class PlanStore:\n    pass\n")


def test_the_prefilter_ignores_a_nested_class():
    """Only module-BODY classes count. A Tool subclass defined inside a function
    is not a registerable tool, and importing its module for that would defeat
    the filter's purpose."""
    src = "def f():\n    class Inner(Tool):\n        pass\n"
    assert not _defines_a_tool_subclass(src)


def test_the_prefilter_survives_a_syntax_error():
    assert not _defines_a_tool_subclass("class Broken(Tool)\n")


def test_real_helper_modules_are_not_imported():
    """Named explicitly, because these are the files the filter exists for."""
    import pathlib

    for rel in ("_infra/presentation.py", "agents/schema.py", "code/_ptc.py"):
        src = (pathlib.Path("src/stackowl/tools") / rel).read_text(encoding="utf-8")
        assert not _defines_a_tool_subclass(src), f"{rel} would be imported needlessly"


# --------------------------------------------------------------------------- #
# Parity with the hand-written list it replaced.
# --------------------------------------------------------------------------- #


def test_discovery_registers_exactly_the_expected_catalog():
    """Every DISCOVERED class registers, and nothing else does.

    D18.6: this asserted `len(names) == 77`. That number was written to prove
    parity with the hand-written list this discovery mechanism replaced — a
    migration check that was valid once and has been a change detector ever
    since, because the platform adds tools on purpose (`tool_build` mints them).

    Comparing the two POPULATIONS is strictly stronger than the count was. It
    still catches a tool silently dropped, and it additionally catches a class
    that is discovered but fails to REGISTER — which a count cannot tell apart
    from an intentional removal, since both just make the number smaller.
    """
    reg = ToolRegistry.with_defaults()
    names = {t.name for t in reg.all()}

    discovered = discover_tool_classes()
    assert len(names) == len(discovered), (
        f"discovery found {len(discovered)} tool classes but the registry holds "
        f"{len(names)} names — a class was discovered and never registered, or two "
        "collapsed onto one name"
    )
    # Spot-check across every registration idiom: plain, shared-store, browser
    # subclass (indirect inheritance), and a meta tool.
    for expected in ("shell", "read_file", "edit", "undo_write", "todo",
                     "browser_navigate", "browser_vision", "tool_search", "owl_build"):
        assert expected in names, f"{expected} was not discovered"


def test_indirect_subclasses_are_found():
    """The 18 browser tools inherit from _BrowserTool, not Tool. They are found
    because _BrowserTool(Tool) is itself top-level in that module, so the file
    passes the AST filter and issubclass picks up everything in it."""
    names = {c.__name__ for c in discover_tool_classes()}
    assert "BrowserNavigateTool" in names
    assert "BrowserClickTool" in names


def test_the_spec_constructed_tool_is_excluded():
    """LearnedShellTool is ONE class that becomes MANY tools — LearnedToolLoader
    builds one per agent-authored JSON spec. Auto-registering the class would add
    a nameless template tool beside the real ones."""
    assert "LearnedShellTool" in EXCLUDED_FROM_DISCOVERY
    assert "LearnedShellTool" not in {c.__name__ for c in discover_tool_classes()}


# --------------------------------------------------------------------------- #
# THE DEFECT. This is the reason discovery yields classes, not instances.
# --------------------------------------------------------------------------- #


def test_edit_apply_patch_and_undo_write_share_ONE_undo_store():
    """Identity, not equality. Each constructor accepts store=None and builds its
    own, so a naive cls() gives three separate stores and undo_write silently
    restores nothing."""
    by = {t.name: t for t in ToolRegistry.with_defaults().all()}
    assert by["edit"]._store is by["apply_patch"]._store
    assert by["edit"]._store is by["undo_write"]._store


def test_todo_and_update_plan_share_ONE_plan_store():
    by = {t.name: t for t in ToolRegistry.with_defaults().all()}
    assert by["todo"]._store is by["update_plan"]._store


def test_a_constructor_parameter_demands_an_explicit_decision():
    """Stricter than 'has a REQUIRED parameter' on purpose: the bug this prevents
    is a DEFAULTED one. EditTool(store=None) constructs perfectly and gets the
    wrong store."""
    from stackowl.tools.io.edit import EditTool
    from stackowl.tools.io.read_file import ReadFileTool

    assert requires_explicit_wiring(EditTool)
    assert not requires_explicit_wiring(ReadFileTool)


def test_an_unwired_tool_with_a_constructor_fails_LOUDLY(monkeypatch):
    """The guard. A future shared-dependency tool that nobody wires must break
    boot, not quietly get a private instance."""
    class RogueTool(Tool):
        def __init__(self, store: object | None = None) -> None:
            self._store = store

        @property
        def name(self) -> str:
            return "rogue"

        @property
        def description(self) -> str:
            return "a tool nobody decided how to construct, for testing the guard"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object):  # pragma: no cover
            raise NotImplementedError

    import stackowl.tools._infra.discovery as disc
    real = disc.discover_tool_classes
    monkeypatch.setattr(
        "stackowl.tools._infra.discovery.discover_tool_classes",
        lambda *a, **k: [*real(), RogueTool],
    )
    with pytest.raises(RuntimeError, match="RogueTool"):
        ToolRegistry.with_defaults()
