"""A Verification step must not name a test file this tree does not have.

WHY THIS EXISTS. D01.7 advertised `tests/memory/test_authored_once_promotion.py` for
TWENTY-FOUR DAYS after `f3d0d85a` deleted it ("remove FactPromoter — the last of the fact
pipeline"), and `authored_once` now appears nowhere in src/ or tests/. Worse, the
document's header said "Last verified 2026-09-07 — Verification section RUN, not trusted".
A step naming a file this tree does not have CANNOT have been run, so the stamp claimed
more than was done. It surfaced only because a `--collect-only` was run by chance while
waiting for a full-suite verdict.

`STALE BY DELETION` could not catch it: that pass only examines documents already flagged
STALE, and D01.7 had been stamped that very day. A freshly-verified document can still
name a deleted test — the gate was on the wrong property.

THE SCOPING IS THE DESIGN, AND IT REPLACES A NEGATION REGEX THAT COULD NOT HAVE WORKED.
MEASURED 2026-09-07: every missing path in this corpus sits in prose that DENIES it —
five sites, five phrasings ("DOES NOT EXIST", "does not exist either", "There is also no
X", "HAS NOT EXISTED SINCE"). `map_freshness._DENIAL` is the careful version of that regex
and matches only THREE of the five, so the best available negation rule would have shipped
a report 40% wrong on a corpus with ZERO real defects.

So this reads no negation at all. A path counts only on a RUNNABLE COMMAND LINE — inside a
fence, not a `#` comment — because that is where a path a reader executes lives, and every
denial is prose or a comment BY CONSTRUCTION.

PROVEN BOTH WAYS. The rule sees 133 paths across the corpus, so it is not blind; it reports
0 today; and against D01.7 at `2e5736b9^` it names line 387 — the real defect — in one
second.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from doc_check import _test_paths_on_command_lines  # noqa: E402


@pytest.mark.tripwire
def test_it_finds_a_path_on_a_runnable_command_line() -> None:
    doc = (
        "## Verification\n\n```bash\n"
        "timeout 400 uv run pytest tests/memory/test_rollover_summary.py \\\n"
        "                          tests/memory/test_authored_once_promotion.py -q\n"
        "# PASS: 25 passed.\n```\n"
    )
    found = {p for _ln, p, _line in _test_paths_on_command_lines(doc)}
    assert found == {
        "tests/memory/test_rollover_summary.py",
        "tests/memory/test_authored_once_promotion.py",
    }, found


@pytest.mark.tripwire
def test_every_denial_shape_in_this_corpus_is_excluded_by_construction() -> None:
    """The five real sites, verbatim. A negation regex catches three; scoping catches all.

    Two are `#` comments INSIDE a fence and three are blockquote prose, which is why
    fence-scoping alone is not enough and the comment rule is load-bearing.
    """
    denials = (
        "```bash\n"
        "#   The earlier draft cited tests/tools/test_registry.py, which DOES NOT EXIST.\n"
        "# [CORRECTED] This also ran tests/memory/test_authored_once_promotion.py and\n"
        "```\n"
        "> 1. `tests/pipeline/steps/test_assemble.py` does not exist. The real files are\n"
        "> cited `tests/tools/test_registry.py`, which does not exist either.\n"
        "> There is also no `tests/providers/test_cost_tracker.py`\n"
    )
    assert _test_paths_on_command_lines(denials) == []


@pytest.mark.tripwire
def test_a_comment_inside_a_fence_is_not_a_command() -> None:
    """The load-bearing half. Both in-fence denials were `#` comments, so a detector
    scoped only to fences would have reported two false positives."""
    doc = "```bash\n#  uv run pytest tests/gone/test_nothing.py\n```\n"
    assert _test_paths_on_command_lines(doc) == []


@pytest.mark.tripwire
def test_prose_outside_a_fence_is_not_a_command() -> None:
    doc = "The suite lives in tests/sessions/test_lane_activity.py and passes.\n"
    assert _test_paths_on_command_lines(doc) == []


@pytest.mark.tripwire
def test_the_scan_actually_reads_the_live_corpus() -> None:
    """THE CONTROL. A silent detector and a clean corpus print the same thing, so assert
    the CAPABILITY — that it still finds real command lines — never that the count is 0.

    Deliberately not pinned to 133: that number grows with ordinary work, and pinning it
    would make every unrelated document edit fail this guard.
    """
    designs = _ROOT / "docs" / "reference-mapping" / "designs"
    total = sum(
        len(_test_paths_on_command_lines(d.read_text(encoding="utf-8")))
        for d in designs.glob("*.md")
    )
    assert total > 50, f"the scan went blind: only {total} test paths seen"
