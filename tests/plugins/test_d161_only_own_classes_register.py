"""A plugin registers the classes it DEFINES, not the ones it imports.

FOUND BY D16.1's BRAINSTORM, 2026-08-15, before any real plugin existed to be
bitten by it — zero are installed on this box, the plugins table has 0 rows, and
the whole surface has never run in anger. So this is a trap set for the FIRST
plugin somebody writes, which is exactly when it would be hardest to diagnose.

THE DEFECT. `_register_classes` walked `dir(module)` and registered every class
that subclassed a known extension-point ABC. `dir()` returns imported names too,
so a plugin doing `from stackowl.tools.web import WebSearchTool` — an entirely
reasonable thing to do while building on an existing tool — registered that tool
as its own. Two plugins importing the same helper would register it twice, each
claiming authorship via `source_name`.

WHY UNDER-REGISTERING IS THE SAFE DIRECTION. The guard treats an unreadable
`__module__` as "not mine". A plugin's own class going missing is visible — the
tool is absent and someone notices. Someone else's class being silently adopted
is not: it works, until it is removed from under you or registered twice.
"""

from __future__ import annotations

import types

from stackowl.plugins.local_loader import LocalPluginLoader


def _module(name: str) -> types.ModuleType:
    return types.ModuleType(name)


class _Defined:
    pass


class TestOwnershipIsDecidedByModule:
    def test_a_class_defined_in_the_module_is_registered(self) -> None:
        mod = _module("acme_plugin")
        _Defined.__module__ = "acme_plugin"

        assert LocalPluginLoader._defined_here(_Defined, mod)

    def test_an_IMPORTED_class_is_not(self) -> None:
        """The bug in one assertion: importing a class must not claim it."""
        mod = _module("acme_plugin")
        _Defined.__module__ = "stackowl.tools.web"

        assert not LocalPluginLoader._defined_here(_Defined, mod)

    def test_a_submodule_of_a_package_plugin_counts_as_its_own(self) -> None:
        """A plugin may organise itself across files — acme.tools inside acme is
        still the plugin's own code, and refusing it would force every plugin
        into a single module."""
        mod = _module("acme")
        _Defined.__module__ = "acme.tools"

        assert LocalPluginLoader._defined_here(_Defined, mod)

    def test_a_lookalike_prefix_does_NOT_count(self) -> None:
        """"acme_evil" starts with "acme" as a string but is a different package.
        The dot is what makes it a submodule."""
        mod = _module("acme")
        _Defined.__module__ = "acme_evil"

        assert not LocalPluginLoader._defined_here(_Defined, mod)


class TestItFailsSafe:
    def test_an_unreadable_module_is_treated_as_not_mine(self) -> None:
        """Under-register (a visibly missing tool) rather than over-register (a
        silent adoption)."""
        mod = _module("acme")
        _Defined.__module__ = ""

        assert not LocalPluginLoader._defined_here(_Defined, mod)

    def test_a_module_with_no_name_is_treated_as_not_mine(self) -> None:
        mod = _module("acme")
        del mod.__name__
        _Defined.__module__ = "acme"

        assert not LocalPluginLoader._defined_here(_Defined, mod)
