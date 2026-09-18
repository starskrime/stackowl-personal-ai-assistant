"""AD-4's ``bridge/`` guard: "``bridge/`` never reads a table generically"
(Story 2.10).

``bridge/`` does not exist yet -- confirmed (spec Boundaries: "Do not build a
``bridge/`` package or a graph-carrier reader -- ``bridge/`` does not exist
yet"). This ships the CHECK now, ahead of the package it will one day guard,
mirroring ``tests/journal/test_narrator.py::test_EventTypeSpec_is_constructed_only_inside_journal``'s
own convention exactly: scan a directory's ``.py`` files for a forbidden
pattern, collect offenders, assert none. It vacuously passes today (no
``bridge/`` directory means nothing to scan) and fires the moment a
function under ``src/stackowl/bridge/`` builds a SQL statement from a
``record_ref``/``locator`` value -- reading whatever table that locator
names GENERICALLY, instead of dispatching through
``journal.records.RecordReaderRegistry``'s one registered, typed,
authority-checked reader per ``(RecordKind, carrier)`` (AD-4).

SCOPE: per-function (and per-module, for top-level code), not same-line.
A same-line regex (``f"SELECT * FROM {locator[...]}"``) misses the ordinary
two-line refactor (``table = locator["table"]`` on one line, the SQL built
from ``table`` on the next) -- a REAL generic read the same regex would
silently pass. Walking each function's own source segment for "a SQL-verb
string AND a locator/record_ref reference, anywhere in this function" (not
requiring them on one line) catches that shape too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE_DIR = _REPO_ROOT / "src" / "stackowl" / "bridge"

#: A SQL verb appearing as a string, ANYWHERE in the scanned scope.
_SQL_VERB_PATTERN = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
#: A `record_ref`/`locator` value in play in that same scope -- exactly the
#: "generic" read AD-4 forbids: a table name pulled out of the record_ref a
#: reader was just handed, rather than one it already knows as a literal
#: constant (see every `read_*_record` in `journal/*_events.py`).
_LOCATOR_PATTERN = re.compile(r"\b(locator|record_ref)\b")


def _offending_scopes(text: str) -> list[str]:
    """Every function (def/async def) whose OWN source segment contains both
    patterns, plus the module itself if its top-level code (outside any
    function) does. Returns names for reporting; ``"<module>"`` for the
    module-level case.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    offenders: list[str] = []
    function_spans: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            segment = ast.get_source_segment(text, node) or ""
            if _SQL_VERB_PATTERN.search(segment) and _LOCATOR_PATTERN.search(segment):
                offenders.append(node.name)
            if node.end_lineno is not None:
                function_spans.add((node.lineno, node.end_lineno))
    # Module (top-level) scope: the whole file minus any line that falls
    # inside a function already checked above -- a locator reference used
    # only inside an innocent function must not flag the module too, and
    # vice versa.
    module_lines = [
        line for i, line in enumerate(text.splitlines(), start=1)
        if not any(start <= i <= end for start, end in function_spans)
    ]
    module_text = "\n".join(module_lines)
    if _SQL_VERB_PATTERN.search(module_text) and _LOCATOR_PATTERN.search(module_text):
        offenders.append("<module>")
    return offenders


@pytest.mark.tripwire
def test_bridge_never_reads_a_journal_table_generically() -> None:
    if not _BRIDGE_DIR.is_dir():
        # bridge/ does not exist yet (spec Boundaries) -- vacuously passes.
        # The moment it exists, this scan runs for real.
        return
    offenders: list[str] = []
    for path in _BRIDGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for scope in _offending_scopes(text):
            offenders.append(f"{path.relative_to(_REPO_ROOT)}::{scope}")
    assert offenders == [], (
        "bridge/ reads a journal record_ref's table generically instead of "
        "dispatching through journal.records.RecordReaderRegistry's one "
        "registered reader per (RecordKind, carrier) (AD-4):\n  "
        + "\n  ".join(offenders)
    )


class TestTheWidenedScanCanActuallyFail:
    """Controls proving the scan is real, in both directions."""

    def test_a_same_line_generic_read_is_caught(self) -> None:
        offending_source = (
            "async def read_it(db_pool, locator):\n"
            "    return await db_pool.fetch_all(\n"
            "        f\"SELECT * FROM {locator['table']} WHERE id = ?\", (row_id,),\n"
            "    )\n"
        )
        assert _offending_scopes(offending_source) == ["read_it"]

    def test_a_two_line_generic_read_is_ALSO_caught(self) -> None:
        """THE control this widening exists for: the SQL-verb string and the
        locator reference live on DIFFERENT lines of the same function -- the
        old same-line regex would have missed this entirely."""
        offending_source = (
            "async def read_it(db_pool, locator):\n"
            "    table = locator['table']\n"
            "    query = f'SELECT * FROM {table} WHERE id = ?'\n"
            "    return await db_pool.fetch_all(query, (row_id,))\n"
        )
        assert _offending_scopes(offending_source) == ["read_it"]

    def test_module_level_generic_reads_are_caught_too(self) -> None:
        offending_source = (
            "table = locator['table']\n"
            "QUERY = f'SELECT * FROM {table}'\n"
        )
        assert _offending_scopes(offending_source) == ["<module>"]

    def test_a_registered_reader_call_site_does_not_match(self) -> None:
        """The compliant shape (dispatching through the registered reader,
        never touching SQL or a table name itself) must never trip the
        scan."""
        compliant_source = (
            "async def read_it(db_pool, locator, record_kind, carrier, owner_id):\n"
            "    reader = get_record_reader_registry().get(record_kind, carrier)\n"
            "    return await reader(db_pool, locator, owner_id=owner_id)\n"
        )
        assert _offending_scopes(compliant_source) == []

    def test_a_locator_only_function_with_no_sql_does_not_match(self) -> None:
        """A function that touches `locator` but never builds SQL (e.g. a
        typed reader's own body, which only ever reads `locator["id"]` to
        pass to a table it already knows as a literal) must not flag --
        only the CO-OCCURRENCE of a SQL verb and a locator/record_ref value
        is the anti-pattern, not using `locator` at all."""
        innocent_source = (
            "async def read_task_record(db_pool, locator, *, owner_id):\n"
            "    return await read_sqlite_record(\n"
            "        db_pool, table=_TABLE, id_column='task_id',\n"
            "        id_value=locator.get('task_id', ''), view_model=TaskRecordView,\n"
            "    )\n"
        )
        assert _offending_scopes(innocent_source) == []
