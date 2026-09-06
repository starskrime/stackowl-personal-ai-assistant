"""Six clusters suppressed for the same reason are ONE finding, not six lines.

WHY THIS EXISTS, measured 2026-09-06 over the 9-day retained log window.

`incident_escalation` drops a failure cluster for three reasons, and it logs
them in two different shapes inside ONE function:

  * `already diagnosed within 24h` — **aggregated**: one line per tick carrying
    `suppressed` (a count) and `signatures` (the list). 743 lines.
  * `too few precisely-attributed rows to recur on` — one line PER CLUSTER.
    **5,579 lines.**
  * `capability fails no more than the platform does` — one line PER CLUSTER.
    325 lines.

6,647 suppression lines across 1,043 ticks — **6.4 lines to say one thing**, ten
minutes apart, naming the SAME six stable capability clusters for nine days.
That is 1% of a 559,697-record log spent restating an unchanged condition, in
the file this programme uses as its primary instrument. CLAUDE.md's own rule is
"count incidents, not log lines"; this is the same error committed by the
emitter rather than by the reader.

THE ROOT CAUSE IS NOT VOLUME, IT IS TWO SHAPES FOR ONE DECISION. The correct
pattern was already chosen, written, and justified twelve hundred lines up in
the same file — `{"suppressed": N, "signatures": [...]}` — and the other two
branches simply did not follow it. Defect shape 3: two copies of one rule, and
the weaker copies are the ones on the hot path.

WHY AGGREGATION AND NOT DEBUG. Demoting these to DEBUG would be the D08.1 defect
this programme has paid for three times: production runs at INFO, and "the
self-heal loop suppressed six clusters this tick" is exactly the evidence
someone needs to explain why no incident was ever raised. The finding stays at
INFO. What changes is that one tick emits one line, carrying the whole picture
instead of a fragment of it.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

_SRC = Path("src/stackowl/scheduler/handlers/incident_escalation.py")


@lru_cache(maxsize=1)
def _tree() -> ast.Module:
    """ONE parse, shared by every helper here.

    This is not an optimisation. The first version of this file parsed the
    module separately in each helper, so the `child is target` identity check
    compared nodes from two DIFFERENT trees and could never match — the
    loop-depth assertion passed while the defect it describes was present, and
    only the sibling assertion failing exposed it. "A test that passes
    immediately may be vacuous", committed by the test's own instrument.
    """
    return ast.parse(_SRC.read_text(encoding="utf-8"))

_PER_CLUSTER_MESSAGES = (
    "too few precisely-attributed ",
    "capability fails no more ",
)


def _log_calls_with(needle: str) -> list[ast.Call]:
    """Every `log.*` call in the module whose message literal contains *needle*."""
    out: list[ast.Call] = []
    for node in ast.walk(_tree()):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not node.args:
            continue
        first = node.args[0]
        parts: list[str] = []
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            parts.append(first.value)
        elif isinstance(first, ast.JoinedStr):
            parts += [
                p.value for p in first.values
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            ]
        if any(needle in p for p in parts):
            out.append(node)
    return out


def _enclosing_loop_depth(target: ast.Call) -> int:
    """How many `for` loops enclose *target* inside the module."""
    best = 0

    def walk(node: ast.AST, depth: int) -> None:
        nonlocal best
        for child in ast.iter_child_nodes(node):
            d = depth + 1 if isinstance(node, ast.For) else depth
            if child is target:
                best = max(best, d)
            walk(child, d)

    walk(_tree(), 0)
    return best


class TestASuppressionIsReportedOncePerTick:
    @pytest.mark.parametrize("needle", _PER_CLUSTER_MESSAGES)
    def test_the_suppression_line_is_not_inside_the_cluster_loop(
        self, needle: str
    ) -> None:
        """THE DEFECT ITSELF. A log call inside `for cluster in clusters:` fires
        once per cluster; six stable clusters therefore restate one unchanged
        condition six times, every ten minutes, for as long as it holds."""
        calls = _log_calls_with(needle)
        assert calls, f"the {needle!r} line has gone — this guard now tests nothing"

        inside = [c for c in calls if _enclosing_loop_depth(c) > 0]
        assert not inside, (
            f"{needle!r} is emitted from inside a for-loop, so it fires once per "
            "cluster instead of once per tick"
        )

    @pytest.mark.parametrize("needle", _PER_CLUSTER_MESSAGES)
    def test_it_reports_a_count_and_the_signatures(self, needle: str) -> None:
        """It must carry the WHOLE picture, matching the shape the third
        suppressor in this same file already uses. A count with no signatures
        says something happened and not what."""
        src = _SRC.read_text(encoding="utf-8")
        call = _log_calls_with(needle)[0]
        segment = ast.get_source_segment(src, call) or ""

        assert "suppressed" in segment, f"{needle!r} reports no count: {segment[:200]}"
        assert "capabilities" in segment or "signatures" in segment, (
            f"{needle!r} reports a count but never says WHICH clusters: {segment[:200]}"
        )

    @pytest.mark.parametrize("needle", _PER_CLUSTER_MESSAGES)
    def test_it_stays_at_INFO(self, needle: str) -> None:
        """Demoting to DEBUG would be the D08.1 defect: production runs at INFO,
        and this line is the evidence for why no incident was ever raised."""
        levels = {c.func.attr for c in _log_calls_with(needle)}  # type: ignore[union-attr]

        assert levels <= {"info", "warning", "error"}, (
            f"{needle!r} is emitted below INFO: {levels}"
        )


class TestTheAggregatedSuppressorIsStillTheModel:
    def test_the_already_diagnosed_line_keeps_its_shape(self) -> None:
        """VACUITY CONTROL and the reference point. The whole argument above is
        that this branch already does it right; if it stopped doing so, the tests
        above would be enforcing a pattern nothing in the file exemplifies."""
        calls = _log_calls_with("already diagnosed within ")
        assert len(calls) == 1, calls
        segment = ast.get_source_segment(_SRC.read_text(encoding="utf-8"), calls[0]) or ""

        assert "suppressed" in segment and "signatures" in segment
        assert _enclosing_loop_depth(calls[0]) == 0
