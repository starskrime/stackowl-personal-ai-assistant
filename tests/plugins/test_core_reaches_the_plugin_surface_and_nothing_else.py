"""D16.2 — "plugins must not touch core", enforced from the core side.

THE RULE, adopted from the reference platform: a plugin may not modify core
entrypoints, and if a plugin needs something the framework does not expose, you
**widen the generic plugin surface** — you never special-case the plugin in core.
There, one PR removed 95 lines of hardcoded plugin argparse from `main.py` for
exactly this reason.

HALF OF IT IS ALREADY ENFORCED, by construction and by two existing tests:

    test_every_declared_extension_point_can_register.py
        set(_ABC_NAMES) == set(loader._registries), both directions
    test_a_contributor_reaches_a_real_prompt.py::TestEveryDeclaredSlotIsActuallyWired
        reads the REAL construction site, because the first test passes even when
        a slot's VALUE is None

That pair exists because the decay mode already happened here once: D08.2 added
`MemoryProvider` to the ABC table and not to the registry table, so
`_registries.get("MemoryProvider")` returned None and registration hit `continue`
— SILENTLY. A memory-provider plugin would have loaded and registered nowhere.

THE OTHER HALF — "core must not special-case a plugin" — cannot be screened
directly today. The decay it forbids looks like `if plugin_name == "foo"`, and
with **zero plugins installed** there is no name to look for: any such screen
would be a zero over a zero denominator, which this repo has already refused
three times this week.

WHAT *CAN* BE MEASURED is the direction of the dependency. Core reaching into the
plugin package is the first move of every special case, and it has a real
denominator: **841 modules in `src/stackowl`, of which seven import
`stackowl.plugins`.** Each one is the generic surface — the three hook-dispatch
seams, and plugin *management* that names no plugin.

SET EQUALITY, NOT SUBSET, AND THAT IS THE POINT. This repo has been bitten by an
allowlist going stale the other way: deleting six modules left three of their
entries in the owner-scope allowlist, and a subset check cannot see that. Both
directions fail loudly, so the list cannot rot in either.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"

#: Modules outside ``stackowl.plugins`` that legitimately reach the plugin surface.
#: Adding to this set is a DESIGN EVENT, not a formality: you are either widening
#: the generic surface (fine — say why here) or special-casing a plugin in core,
#: which is the thing D16.2 forbids.
_SURFACE_CALLERS = {
    # The three seams that DISPATCH lifecycle hooks. They import `hooks` and name
    # a hook POINT, never a plugin.
    "tools/base.py",
    "sessions/store.py",
    "providers/base.py",
    # Boot: the one place that constructs the loader and hands it every registry.
    "startup/orchestrator.py",
    # Plugin MANAGEMENT — install, list, enable, verify. Generic by construction:
    # these operate on whatever is installed and none of them names a plugin.
    "cli/app.py",
    "commands/assembly.py",
    "commands/permissions.py",
    "commands/plugins_command.py",
}


def _modules_importing_plugins() -> set[str]:
    found: set[str] = set()
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        if rel.startswith("plugins/"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not our concern
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "stackowl.plugins"
            ):
                found.add(rel)
            elif isinstance(node, ast.Import):
                if any(a.name.startswith("stackowl.plugins") for a in node.names):
                    found.add(rel)
    return found


@pytest.mark.tripwire
def test_only_the_surface_callers_reach_into_the_plugin_package() -> None:
    actual = _modules_importing_plugins()

    unexpected = actual - _SURFACE_CALLERS
    assert not unexpected, (
        "core module(s) newly reaching into stackowl.plugins: "
        f"{sorted(unexpected)}.\nD16.2: widen the generic plugin surface instead of "
        "special-casing a plugin in core. If this IS a widening, add it to "
        "_SURFACE_CALLERS with the reason."
    )

    stale = _SURFACE_CALLERS - actual
    assert not stale, (
        f"_SURFACE_CALLERS names module(s) that no longer import the plugin package: "
        f"{sorted(stale)}.\nA subset check would not have seen this — an allowlist "
        "that rots is how three dead entries survived the owner-scope list."
    )


@pytest.mark.tripwire
def test_the_denominator_is_real() -> None:
    """A guard over an empty set proves nothing. This one runs over the whole tree."""
    total = sum(1 for _ in _SRC.rglob("*.py"))
    assert total > 500, f"expected the whole src tree, scanned {total} files"
    assert 0 < len(_SURFACE_CALLERS) < total // 10, (
        "the surface should be a small, nameable set — if it grows to a tenth of "
        "the tree it has stopped being a surface"
    )


@pytest.mark.tripwire
@pytest.mark.parametrize("seam", ["tools/base.py", "sessions/store.py", "providers/base.py"])
def test_a_dispatch_seam_names_a_hook_point_not_a_plugin(seam: str) -> None:
    """The seams are the one place core touches plugin code on every turn.

    They must reference hook POINTS — which are part of the generic surface — and
    never a plugin's name or module. This is the closest thing to a direct screen
    for "no special-casing" that has a non-zero denominator today.
    """
    src = (_SRC / seam).read_text(encoding="utf-8")

    assert "hooks.dispatch(" in src, f"{seam} should dispatch, or it is not a seam"
    assert "stackowl.plugins.local_loader" not in src, (
        f"{seam} reaches into the LOADER — the surface is hooks, not the machinery"
    )
    assert "stackowl.plugins.boot" not in src
