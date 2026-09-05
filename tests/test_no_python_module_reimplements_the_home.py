"""D18.4 — the accessor rule holds in Python by the care of its authors alone.

`StackowlHome` is the single source of truth for every path under the StackOwl
home. Measured 2026-09-05, the rule HOLDS: there is no `Path.home() / ".stackowl"`
anywhere in `src/` outside `paths.py`, and the four other `Path.home()` uses are
legitimately NOT StackOwl state — the systemd user unit directory
(`service/installer.py`), macOS LaunchAgents (same file), and the XDG cache root
(`startup/browser_probe.py`).

**Nothing asked the question when the next one was added.** That is the same gap
D18.1 identified for environment variables and D18.3 for shell scripts, and it is
one rule expressed once per LANGUAGE rather than three copies of a rule: a guard
that reads `*.sh` cannot see a `.py`, and vice versa. D18.3's guard globs `*.sh`
only, so the Python case was not one short — it was zero.

THE REFERENCE PLATFORM IS THE ARGUMENT FOR THIS FILE. It has the same rule written
in its contributor guide, a display helper built on it, and **no automated guard at
all** — and its tree now carries roughly twenty production files that construct
their home by hand, including a script that reaches the operator's real credential
file. They shipped the ergonomics and skipped the enforcement, and the enforcement
is the half that decayed. This is the half we keep.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src" / "stackowl"

#: `paths.py` IS the accessor — it is the one place allowed to say `.stackowl`.
_THE_ACCESSOR = "paths.py"

#: Legitimate `Path.home()` uses that are NOT StackOwl state. Each is an
#: OS-standard location that must NOT move when STACKOWL_HOME moves: a user
#: systemd unit belongs in ~/.config/systemd/user wherever StackOwl keeps its
#: data, and the XDG cache root is defined by the spec, not by us.
_NON_STACKOWL_HOME_USES = {
    "service/installer.py": "systemd user units and macOS LaunchAgents",
    "startup/browser_probe.py": "the XDG cache root",
}


def _string_constants(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@pytest.mark.tripwire
def test_no_module_builds_the_stackowl_home_by_hand() -> None:
    """A `.stackowl` path literal outside the accessor is the defect.

    Docstrings and comments naming `~/.stackowl/...` are fine and plentiful — they
    are documentation. Only a STRING CONSTANT that is a path fragment counts, which
    is why this parses rather than greps: a grep cannot tell a docstring from a
    path, and this file would be 90% false positives if it tried.
    """
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for path in sorted(_SRC.rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if rel == _THE_ACCESSOR:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        scanned += 1
        # Docstrings are ast.Constant too, so exclude every docstring explicitly.
        docstrings = {
            ast.get_docstring(n, clean=False)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        hits = [
            s for s in _string_constants(tree)
            if s not in docstrings and (s == ".stackowl" or s.startswith(".stackowl/"))
        ]
        if hits:
            offenders[rel] = hits

    assert scanned > 300, f"expected the real tree, parsed {scanned} modules"
    assert not offenders, (
        f"module(s) building the StackOwl home by hand: {offenders}\n"
        "D18.4: every path under the home comes from `StackowlHome`. A hand-built "
        "path ignores STACKOWL_HOME, so it reads the operator's real home while the "
        "rest of the process reads an isolated one — including under the test suite."
    )


@pytest.mark.tripwire
def test_path_home_outside_the_accessor_is_justified() -> None:
    """`Path.home()` is not banned — it is CLASSIFIED, like D18.1's env vars.

    Some paths genuinely belong to the OS rather than to StackOwl. The rule is not
    "never call it" but "say which of the two this is", so the next one is a
    decision instead of an accident.
    """
    users: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if rel == _THE_ACCESSOR:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        count = sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "home"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "Path"
        )
        if count:
            users[rel] = count

    unjustified = sorted(set(users) - set(_NON_STACKOWL_HOME_USES))
    assert not unjustified, (
        f"unclassified Path.home() use(s): {unjustified}\n"
        "D18.4: if this path is StackOwl state, get it from `StackowlHome` so it "
        "follows STACKOWL_HOME. If it is an OS-standard location that must NOT "
        "move with the home (a systemd unit, an XDG cache), classify it here with "
        "that reason."
    )

    stale = sorted(set(_NON_STACKOWL_HOME_USES) - set(users))
    assert not stale, (
        f"classified but no longer calling Path.home(): {stale}. Remove them — a "
        "list that outlives its subjects stops describing anything."
    )
