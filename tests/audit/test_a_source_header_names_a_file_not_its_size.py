"""A document's `Source:` names WHERE the code is, never HOW BIG it was.

WHY THIS EXISTS, measured 2026-09-06.

`DOC_STANDARD`'s own template prescribes::

    > **Source:** `src/stackowl/<path>` (~N lines)

so every document is asked to record a MEASUREMENT that starts rotting the
moment it is written. Fourteen documents took the advice. **THIRTEEN OF THE
FOURTEEN ARE NOW WRONG**, and not narrowly:

    D08.1  curated.py                531  ->  1161   (+630, more than doubled)
    D01.1  assemble.py               437  ->   630
    D16.3  assemble.py               478  ->   630
    D01.1  base_prompt.py            239  ->   352
    D08.2  providers.py              220  ->   322
    D01.1  prompt_store.py           168  ->   245

The fourteenth is off by one. Not a single count is right, and the one nearest
the truth is nearest by accident.

THIS IS NOT COSMETIC. "curated.py (531 lines)" tells a reader the file is half
its real size before they open it — a wrong mental model of the component,
handed over by the document meant to explain it.

THE ROOT CAUSE IS UPSTREAM OF EVERY DOCUMENT. This is DEBT-150's finding one
level up: a package-wide `expect: N passed` rotted because adding a test is the
programme working, and a `(~N lines)` rots because editing the code is. There the
fix was to assert a shape; here the fix is to delete the field from the STANDARD,
because a standard that prescribes a rotting measurement will keep producing
them however many individual documents are corrected.

The PATH is the property and stays. The SIZE is a measurement `wc -l` can answer
in the moment anyone actually cares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"
_STANDARD = _ROOT / "docs" / "reference-mapping" / "DOC_STANDARD.md"

#: `path.py` (437 lines) / (~437) / (437) — a count attached to a cited file.
_PINNED = re.compile(r"`[A-Za-z0-9_./-]+\.(?:py|sql|sh|md)(?:::[A-Za-z_][A-Za-z0-9_]*)?`\s*\(~?\d+")


def _source_lines() -> list[tuple[str, str]]:
    out = []
    for f in sorted(_DESIGNS.glob("*.md")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("> **Source:**"):
                out.append((f.name, line.strip()))
    return out


@pytest.mark.tripwire
def test_no_source_header_pins_a_line_count() -> None:
    """THE DEFECT ITSELF. A size recorded in prose is wrong the next time anyone
    edits the file, and 13 of 14 already were."""
    offenders = [
        f"{name}: {m.group(0)}"
        for name, line in _source_lines()
        if (m := _PINNED.search(line))
    ]

    assert not offenders, (
        "these `Source:` headers pin a line count, which is wrong the next time "
        "the file is edited — name the path and let `wc -l` answer the size:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.tripwire
def test_the_standard_no_longer_asks_for_one() -> None:
    """THE ROOT CAUSE. Correcting the documents while the template still says
    `(~N lines)` guarantees the next author reintroduces it — the same reason
    correcting one copy of a rule is not correcting the rule."""
    text = _STANDARD.read_text(encoding="utf-8")
    template = [ln for ln in text.splitlines() if ln.strip().startswith("> **Source:**")]

    assert template, "the standard no longer shows a Source line at all"
    for line in template:
        assert "lines)" not in line, (
            f"DOC_STANDARD still prescribes a line count: {line.strip()}"
        )


def test_the_guard_sees_a_real_population() -> None:
    """VACUITY CONTROL: the assertion above passes over an empty list by design,
    so a broken glob or a changed header prefix would look like compliance."""
    sources = _source_lines()

    assert len(sources) >= 25, f"only found {len(sources)} Source headers"
    assert any("src/stackowl" in line for _n, line in sources), (
        "no Source header cites a real path — the parser is matching the wrong line"
    )


def test_the_pattern_catches_the_shapes_that_were_actually_used() -> None:
    """The three spellings found in the corpus, pinned so a fourth does not slip
    past by being punctuated differently."""
    assert _PINNED.search("> **Source:** `src/a/b.py` (437 lines)")
    assert _PINNED.search("> **Source:** `src/a/b.py` (~437 lines)")
    assert _PINNED.search("> **Source:** `src/a/b.py` (239), `src/c.py`")
    # The FOURTH spelling, found only because the first sweep left it behind:
    # `(133 lines, new)` — a count with a genuine annotation beside it. The
    # count goes, the annotation stays.
    assert _PINNED.search("> **Source:** `src/a/b.py` (133 lines, new)")
    # The FIFTH: a count on a SYMBOL rather than a file. D09.6's `(~93 lines)`
    # survived two sweeps because the pattern required the backtick to close
    # right after `.py`. Its count happens to be RIGHT today — removed anyway,
    # because the guard is about the shape, not about today's luck.
    assert _PINNED.search("> **Source:** `src/a/b.py::Thing` (~93 lines)")
    assert not _PINNED.search("> **Source:** `src/a/b.py`, `src/c/d.py`"), (
        "a plain path list must NOT be flagged"
    )
