"""A reader that opens SQLite with a bare path creates the database it meant to read.

WHY THIS EXISTS. `sqlite3.connect(path)` creates `path` when it is missing. That is how
`~/.stackowl/stackowl.db` came back as a 0-byte decoy, and MEASURED 2026-09-12 the same
shape sat in `src/`: `stackowl db restore` created a mistyped backup path and then
reported `integrity_check ok` on the empty file it had just made, and `/audit export`,
the TUI's onboarding lookup, `PluginRegistry.list`/`exists` and
`AuditLogger.verify_chain` all read through a connect that could create.

THE RULE. Every `sqlite3.connect(...)` in `src/` either opens read-only through
`stackowl.db.readonly` (`connect_read_only`, or a `read_only_uri(...)` argument) or sits
in a function classified below as a WRITER, with its reason. A new reader has to choose,
in writing — and an entry whose function no longer opens by bare path must go.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"

#: `file::function` -> why this connection must be able to write.
_WRITERS: dict[str, str] = {
    "audit/logger.py::append": "appends the integrity-chained audit row",
    "audit/logger.py::tail": "creates the audit schema on first use before it reads",
    "audit/retention.py::prune": "deletes expired audit rows",
    "cli/identity_cli.py::relink": "re-keys identity rows",
    "db/migrations/runner.py::_execute": "applies a migration",
    "db/migrations/runner.py::_verify_applied": "repairs the migration ledger",
    "export/backup.py::backup": "writes the backup file with VACUUM INTO",
    "plugins/registry.py::install": "inserts the plugin row",
    "plugins/registry.py::set_enabled": "updates the plugin row",
    "plugins/registry.py::uninstall": "deletes the plugin row",
    "tui/widgets/parliament_panel_helpers.py::mark_shown": "records that a tip was shown",
}


class _Connects(ast.NodeVisitor):
    """Every `sqlite3.connect(...)`, with its enclosing function and whether it is read-only."""

    def __init__(self) -> None:
        self._stack = ["<module>"]
        self.found: list[tuple[str, bool]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sqlite3"
        ):
            first = node.args[0] if node.args else None
            read_only = isinstance(first, ast.Call) and "read_only_uri" in (
                getattr(first.func, "id", None), getattr(first.func, "attr", None)
            )
            self.found.append((self._stack[-1], read_only))
        self.generic_visit(node)


def _bare_connects(root: pathlib.Path) -> tuple[int, set[str]]:
    """(connect calls seen, `file::function` keys that open by bare path)."""
    seen = 0
    bare: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        visitor = _Connects()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        seen += len(visitor.found)
        rel = path.relative_to(root).as_posix()
        bare.update(f"{rel}::{fn}" for fn, read_only in visitor.found if not read_only)
    return seen, bare


@pytest.mark.tripwire
def test_every_bare_sqlite_connect_in_src_is_a_classified_writer() -> None:
    seen, bare = _bare_connects(_SRC)

    assert seen, "found no sqlite3.connect at all — the walk is broken, not the tree"
    unclassified = sorted(bare - set(_WRITERS))
    assert not unclassified, (
        f"sqlite3.connect by bare path, not classified as a writer: {unclassified}\n"
        "A bare connect CREATES a missing database. A reader opens through "
        "`stackowl.db.readonly.connect_read_only`; a writer is added to `_WRITERS` with "
        "the reason it must be able to write."
    )
    stale = sorted(set(_WRITERS) - bare)
    assert not stale, (
        f"classified as writers but no longer opening by bare path: {stale}. Remove them "
        "— a list that outlives its subjects stops describing anything."
    )


@pytest.mark.tripwire
def test_the_connect_scan_can_actually_fail(tmp_path: pathlib.Path) -> None:
    """VACUITY CONTROL on a population this test BUILDS."""
    (tmp_path / "reader.py").write_text(
        "import sqlite3\ndef read(p):\n    return sqlite3.connect(p)\n", encoding="utf-8"
    )
    (tmp_path / "safe.py").write_text(
        "import sqlite3\nfrom stackowl.db.readonly import read_only_uri\n"
        "def read(p):\n    return sqlite3.connect(read_only_uri(p), uri=True)\n",
        encoding="utf-8",
    )
    (tmp_path / "top.py").write_text(
        "import sqlite3\nCONN = sqlite3.connect('x.db')\n", encoding="utf-8"
    )

    seen, bare = _bare_connects(tmp_path)

    assert seen == 3
    assert bare == {"reader.py::read", "top.py::<module>"}
