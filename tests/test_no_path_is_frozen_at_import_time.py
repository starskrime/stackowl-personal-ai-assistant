"""D18.3 — one module-level constant froze a runtime-resolvable path.

`StackowlHome` accessors read the environment at CALL time, and measured
2026-09-05 every one of the 25 that return a Path is contained by
``STACKOWL_HOME`` — 0 escapes. That is what makes an isolated second home
possible at all.

**One module-level constant opted out of it**::

    src/stackowl/plugins/index.py:15
    _CONFIG_BASE = StackowlHome.plugins_dir()

Demonstrated, with the full CLI import graph loaded first and the variable then
set in-process the way a ``--profile`` flag would set it::

    StackowlHome.plugins_dir() -> /tmp/h-late/plugins      follows the switch
    PluginIndex()._path        -> /tmp/h-early/plugins/... FROZEN

So the single exception to an otherwise call-time-correct codebase was exactly
the thing that would have made a profile flag silently wrong — reading one
home's plugin index while every other subsystem read the other's.

**HONEST SCOPE, because a panel lens measured it and it matters.** An AST sweep of
every module-level assignment, class body, default argument and decorator in
``src/`` found this to be the ONLY eager module-level call of an environment-
dependent path accessor. It is not a class of bug, and it caused no measured harm:
every ``src/`` importer of ``plugins.index`` is function-local, so on the
production path the constant resolves at first call, after the environment is
already set. The hazard was latent and its trigger was a feature that does not
exist yet.

That is precisely why a guard is the right size of response rather than a
refactor: the property "no path is decided at import time" is currently true
everywhere, is cheap to keep true, and is load-bearing for anything that ever
switches home in-process — profiles, and the test isolation in
``tests/conftest.py`` that already does exactly this.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stackowl"

#: Accessors whose value depends on the environment at the moment they are called.
_ENV_DEPENDENT = {"StackowlHome"}
_ENV_READS = {"getenv", "environ"}


def _module_level_env_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """Module-level assignments whose value CALLS an environment-dependent accessor.

    Only the module body is walked. A call inside a function or a
    ``default_factory=`` lambda is lazy and correct — that distinction is the
    whole point, so it must not be flattened by a recursive walk over everything.
    """
    found: list[tuple[str, int]] = []
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        for sub in ast.walk(value):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in _ENV_DEPENDENT:
                    found.append((f"{func.value.id}.{func.attr}()", node.lineno))
                elif func.value.id == "os" and func.attr in _ENV_READS:
                    found.append((f"os.{func.attr}()", node.lineno))
    return found


#: `webhooks/rate_limit.py` reads a DEPLOYMENT SECRET at import, not a path. Its own
#: comment states it must be stable across the deployment, and D18.1 classified
#: STACKOWL_FINGERPRINT_SECRET as `deployment-secret` for that reason. It is
#: environment-dependent on purpose and switching home must NOT change it.
_ALLOWED = {"webhooks/rate_limit.py"}


@pytest.mark.tripwire
def test_no_module_level_constant_freezes_an_environment_path() -> None:
    offenders: dict[str, list[tuple[str, int]]] = {}
    scanned = 0
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a file that does not parse
            continue
        scanned += 1
        hits = _module_level_env_calls(tree)
        rel = str(path.relative_to(_SRC.parent.parent / "src" / "stackowl"))
        if hits and rel not in _ALLOWED:
            offenders[rel] = hits

    assert scanned > 300, f"expected the real tree, parsed {scanned} modules"
    assert not offenders, (
        f"a path was decided at IMPORT time: {offenders}\n"
        "D18.3: StackowlHome reads the environment at CALL time, which is what lets "
        "STACKOWL_HOME isolate an instance and what lets tests/conftest.py redirect "
        "the home. A module-level constant freezes it to whatever the environment "
        "said when the module first loaded, so the process then reads one home while "
        "every other subsystem reads another — silently. Call the accessor where you "
        "need it, or use Field(default_factory=...)."
    )


def test_the_allowlist_still_describes_something_real() -> None:
    """An allowlist that outlives its subject stops describing anything."""
    for rel in _ALLOWED:
        assert (_SRC / rel).exists(), f"allowlisted {rel} no longer exists — drop it"
