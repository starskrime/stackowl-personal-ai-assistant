"""One eviction path was observable and the other was dark. Same decision, same class.

MEASURED 2026-09-09, verifying a product defect somebody else's lens had recorded as
*"85% of conversation summaries evicted — the assistant forgets summarised conversations
after ~50 rollovers"*, on the vision pillar "Memory that actually knows the user".

THE DEFECT WAS REAL AND IS ALREADY FIXED, and the proof was sitting in production unread.
`[memory] sqlite_bridge.stage: exit` is INFO and carries `trimmed`, so the whole history
is countable:

    2026-08-28  events=56  trimmed=56      2026-09-02  events=4  trimmed=4
    2026-08-29  events=59  trimmed=59      2026-09-03  events=2  trimmed=2
    2026-08-30  events=69  trimmed=69      2026-09-06  events=2  trimmed=2
    2026-08-31  events=59  trimmed=59      2026-09-08  events=3  trimmed=0
    2026-09-01  events=72  trimmed=72      2026-09-09  events=1  trimmed=0

323 of 327 staged summaries deleted — every single staging event evicting one — until
`2d42ce6b` landed the `embedding IS NULL` predicate on 2026-09-08, after which the count
is ZERO. All 54 surviving rows are embedded, so the bound can no longer reach them. That
is a clean before/after in production, and nothing asked for it: the fix shipped with no
acceptance check, so its success was invisible and the record still listed the defect as
open. A FIX WITHOUT A CHECK CANNOT BE TOLD FROM A FLUKE, and the next reader re-opens it.

AND THE MIRROR PATH IS DARK. `store()` bounds the conversation buffer with `_trim_turns`
and reports the rowcount in a **DEBUG** exit line, while `stage()` reports its own in an
**INFO** one. Production runs at INFO. So eviction of the user's short-term memory —
the same decision, about content the user typed rather than a generated summary — leaves
no record at all, and the identical question could not be asked about it. That asymmetry
is why this file exists rather than a note on the closure.

WHY IT IS EMITTED ONLY WHEN SOMETHING IS DELETED. An INFO on every write would be one
line per turn to say nothing happened, which is how a log becomes unreadable and how a
real event hides. The branch that matters is the one that REMOVES a memory.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from stackowl.memory import sqlite_bridge

_SRC = Path(sqlite_bridge.__file__)


def _log_calls(func_name: str) -> list[tuple[str, str]]:
    """(level, first literal) for every `log.*` call in one function."""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            attr = call.func
            if not (isinstance(attr.value, ast.Attribute) and attr.value.attr == "memory"):
                continue
            literal = ""
            if call.args and isinstance(call.args[0], ast.Constant):
                literal = str(call.args[0].value)
            out.append((attr.attr, literal))
    return out


class TestBothEvictionPathsAreVisibleInProduction:
    @pytest.mark.tripwire
    def test_the_conversation_buffer_trim_reports_at_INFO(self) -> None:
        """The dark half. `_trim_turns` deletes the user's own turns; its only witness
        was a DEBUG exit line, and production runs at INFO."""
        levels = {lvl for lvl, _lit in _log_calls("store")}

        assert "info" in levels, (
            "`store` has no INFO line — conversation-turn eviction is invisible in "
            "production, which is the defect this file exists about"
        )

    @pytest.mark.tripwire
    def test_the_staged_fact_trim_still_reports_at_INFO(self) -> None:
        """The half that already worked, pinned so a tidy-up cannot quietly demote it.
        This line is the ONLY reason the 323-row eviction could be measured at all."""
        assert any(
            lvl == "info" and "stage: exit" in lit for lvl, lit in _log_calls("stage")
        ), "the one witness that made the memory defect measurable has been demoted"

    @pytest.mark.tripwire
    def test_the_eviction_line_carries_the_COUNT_not_just_the_event(self) -> None:
        """A witness that says "a trim ran" and not "it deleted 56" cannot separate a
        healthy bound from one eating the archive. The whole before/after above is the
        `trimmed` FIELD, not the line's existence."""
        source = inspect.getsource(sqlite_bridge.SqliteMemoryBridge.store)

        assert "trimmed" in source
        assert '"trimmed": trimmed' in source, (
            "the count is no longer carried as a field a query can group by"
        )

    def test_it_speaks_only_when_a_memory_is_actually_removed(self) -> None:
        """Not a line per write. An INFO that fires on every turn to report zero is how
        a log becomes unreadable, and how the one event that matters hides inside it."""
        source = inspect.getsource(sqlite_bridge.SqliteMemoryBridge.store)

        assert "if trimmed" in source, (
            "the INFO is unconditional — it will print one line per turn saying nothing "
            "happened"
        )
