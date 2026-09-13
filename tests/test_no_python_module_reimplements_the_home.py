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
_SCRIPTS = _ROOT / "scripts"

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


def _non_docstring_constants(tree: ast.AST) -> list[str]:
    """String constants that are not docstrings — docstrings are ast.Constant too."""
    docstrings = {
        ast.get_docstring(n, clean=False)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return [s for s in _string_constants(tree) if s not in docstrings]


def _hand_built_home_literals(tree: ast.AST) -> list[str]:
    return [
        s for s in _non_docstring_constants(tree)
        if s == ".stackowl" or s.startswith(".stackowl/")
    ]


def _path_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names this file binds to `pathlib.Path`, and to the `pathlib` module."""
    path_names, module_names = {"Path"}, {"pathlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            path_names.update(a.asname or a.name for a in node.names if a.name == "Path")
        elif isinstance(node, ast.Import):
            module_names.update(a.asname or a.name for a in node.names if a.name == "pathlib")
    return path_names, module_names


def _path_home_calls(tree: ast.AST) -> int:
    """`Path.home()`, `pathlib.Path.home()`, and either one under an import alias.

    The second spelling walked past the first version of this match: on 2026-09-12
    `scripts/retired_log_messages.py` built its default with `pathlib.Path.home()`.
    """
    path_names, module_names = _path_aliases(tree)
    count = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "home"
        ):
            continue
        owner = node.func.value
        if (isinstance(owner, ast.Name) and owner.id in path_names) or (
            isinstance(owner, ast.Attribute)
            and owner.attr == "Path"
            and isinstance(owner.value, ast.Name)
            and owner.value.id in module_names
        ):
            count += 1
    return count


def _expanduser_home_literals(tree: ast.AST) -> list[str]:
    """`os.path.expanduser("~/.stackowl")` and `Path("~/.stackowl").expanduser()`."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and "expanduser" in (
            getattr(node.func, "attr", None), getattr(node.func, "id", None)
        ):
            hits.extend(s for s in _string_constants(node) if ".stackowl" in s)
    return hits


def _hand_built_db_literals(tree: ast.AST) -> list[str]:
    """`"stackowl.db"`, and the literal tail of `f"{workspace}/stackowl.db"`."""
    return [s for s in _non_docstring_constants(tree) if s.endswith("stackowl.db")]


def _script_offenders(root: pathlib.Path) -> tuple[int, dict[str, list[str]]]:
    """(files parsed, file -> hand-built StackOwl paths) for every script under root."""
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        scanned += 1
        hits = _hand_built_home_literals(tree) + _expanduser_home_literals(tree)
        hits += ["Path.home()"] * _path_home_calls(tree)
        hits += _hand_built_db_literals(tree)
        if hits:
            offenders[str(path.relative_to(root))] = hits
    return scanned, offenders


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
        hits = _hand_built_home_literals(tree)
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
        count = _path_home_calls(tree)
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


@pytest.mark.tripwire
def test_no_script_builds_the_home_or_the_database_path_by_hand() -> None:
    """SCRIPTS READ THE OPERATOR'S REAL STATE, and the scans above read `src/` only.

    MEASURED 2026-09-12: `retired_log_messages.py` defaulted `--logs` to
    `pathlib.Path.home() / ".stackowl" / "logs"`, and `migrate_lessons_from_lancedb.py`
    and `duplicate_answers.py` joined `"stackowl.db"` onto the workspace by hand. A
    script that re-derives a path ignores STACKOWL_HOME, and a hand-built database
    path is how an ad-hoc reader lands on the empty decoy beside the config. No script
    has a reason to name the database FILE: `StackowlHome.db_path()` is the answer, and
    no script has an OS-standard location to classify, so `Path.home()` is simply out.
    """
    scanned, offenders = _script_offenders(_SCRIPTS)

    assert scanned, "parsed no scripts — the walk is broken, not the tree"
    assert not offenders, (
        f"script(s) building a StackOwl path by hand: {offenders}\n"
        "Resolve it through `StackowlHome` (`db_path()`, `logs_dir()`, …) so the script "
        "reads the instance that is actually running."
    )


@pytest.mark.tripwire
def test_the_script_scan_can_actually_fail(tmp_path: pathlib.Path) -> None:
    """VACUITY CONTROL on a population this test BUILDS, never a floor on the live count.

    A floor under the scripts tree fails the day a cleanup shrinks it. Each spelling the
    scan claims to catch is planted here once, beside a clean file that only names the
    path in a docstring, so a matcher that goes dead fails by name.
    """
    planted = {
        "home_literal.py": "from pathlib import Path\nLOGS = Path.home() / '.stackowl' / 'logs'\n",
        "db_fstring.py": "ws = 'x'\nDB = f'{ws}/stackowl.db'\n",
        "expanduser.py": "import os\nHOME = os.path.expanduser('~/.stackowl')\n",
        "aliased_class.py": "from pathlib import Path as P\nROOT = P.home()\n",
        "aliased_module.py": "import pathlib as pl\nROOT = pl.Path.home()\n",
        "clean.py": (
            '"""Reads ~/.stackowl/workspace/stackowl.db."""\n'
            "from stackowl.paths import StackowlHome\nDB = StackowlHome.db_path()\n"
        ),
    }
    for name, body in planted.items():
        (tmp_path / name).write_text(body, encoding="utf-8")

    scanned, offenders = _script_offenders(tmp_path)

    assert scanned == len(planted)
    assert sorted(offenders) == sorted(set(planted) - {"clean.py"}), offenders
