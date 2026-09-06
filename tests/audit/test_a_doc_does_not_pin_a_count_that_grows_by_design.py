"""A Verification step over a whole package must assert a SHAPE, not a count.

WHY THIS EXISTS, measured 2026-09-06 across the 83 design documents.

`DOC_STANDARD` requires a runnable Verification section, and 30 of its `expect:`
lines pin a test COUNT. Those split cleanly in two:

  * **22 name ONE test file.** That count changes only when that file changes,
    and then its document is being edited anyway. Stable, meaningful, and left
    alone — they pin the coverage the document is actually about.
  * **7 name a whole package or a marker** (`tests/skills`, `tests/sandbox`,
    `-m tripwire`). Those change whenever ANYONE adds a test anywhere in the
    package — which is the normal, desirable work of this programme. **The
    document goes stale precisely BECAUSE the programme is working.**

FOUR OF THE SEVEN HAD ALREADY DRIFTED when measured:

  | document | pins | actual |
  |---|---|---|
  | D10.1 `tests/skills` | 474 | 495 |
  | D10.4 `tests/skills` | 484 | 495 |
  | D10.7 `tests/skills tests/commands` | 871 | 885 |
  | D16.4 `-m tripwire` | 58 | **139** |

D10.1 and D10.4 pin DIFFERENT numbers for the IDENTICAL command, which is the
mechanism in one line: each froze the number on the day it was written. D16.4 is
2.4x off. The remaining three (`tests/sandbox` 172, `tests/tools/agents` 125,
`tests/tools/knowledge` 205) happened to still be right — not by construction,
but because nobody has added a test there lately.

THE CORRECT SHAPE WAS ALREADY THE NORM AND ALREADY IN THE SAME FILES. Thirteen
wide commands assert `expect: 0 failed`, against seven that pin a count — and
D03.2 does BOTH, three lines apart: a single-file `expect: 14 passed` and then
`uv run pytest tests/pipeline/steps tests/memory -q` / `expect: 0 failed`. So
this is defect shape 3 with the correct copy already dominant, and DEBT-133
named the same disease for LOG counts ("SHAPE, NOT A PINNED COUNT") without
anyone looking at the test counts.

A NOTE ON THE PARSER. An earlier version of this guard reported D03.2:131 as an
offender. It is a line CONTINUATION — `... \` then `-q -k "WIRED"` — and the
walker had taken the continuation for the command. Backslash joins happen before
anything else here, because a guard that cries wolf on correct work is the
failure this repo pays for most often.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DESIGNS = Path(__file__).resolve().parents[2] / "docs" / "reference-mapping" / "designs"


def _steps(text: str) -> list[tuple[int, str, str]]:
    """(line_no, expect_text, the command it belongs to) for every `expect:`."""
    raw = text.splitlines()
    joined: list[str | None] = []
    buf = ""
    for ln in raw:
        s = ln.rstrip()
        if s.endswith("\\"):           # a continuation is part of ONE command
            buf += s[:-1] + " "
            joined.append(None)
            continue
        joined.append((buf + s) if buf else s)
        buf = ""
    out: list[tuple[int, str, str]] = []
    for i, ln in enumerate(joined):
        if ln is None:
            continue
        m = re.match(r"^#\s*expect:\s*(.*)", ln.strip())
        if not m:
            continue
        cmd = ""
        for c in reversed([x for x in joined[max(0, i - 12):i] if x is not None]):
            if c.strip() and not c.strip().startswith("#"):
                cmd = c.strip()
                break
        out.append((i + 1, m.group(1), cmd))
    return out


def _is_wide(cmd: str) -> bool:
    """True when the pytest target is anything other than exactly ONE file."""
    if "pytest" not in cmd:
        return False
    targets = re.findall(r"(?:^|\s)(tests/[^\s]+)", cmd)
    if "-m " in cmd or not targets:
        return True
    return len(targets) > 1 or any(not t.endswith(".py") for t in targets)


def _offenders() -> list[str]:
    out: list[str] = []
    for f in sorted(_DESIGNS.glob("*.md")):
        for line_no, expect, cmd in _steps(f.read_text(encoding="utf-8")):
            if _is_wide(cmd) and re.search(r"\b\d+\s+passed\b", expect):
                out.append(f"{f.name}:{line_no}  `{cmd[:60]}`  expect: {expect[:40]}")
    return out


@pytest.mark.tripwire
def test_no_package_wide_step_pins_a_test_count() -> None:
    """THE DEFECT ITSELF. Adding a test anywhere in the package falsifies the
    document, so the record rots as a side effect of doing the work."""
    offenders = _offenders()

    assert not offenders, (
        "these Verification steps run over a whole package or marker and pin a "
        "COUNT, so any new test anywhere falsifies them — assert `0 failed` "
        "instead, as 13 other wide steps already do:\n  " + "\n  ".join(offenders)
    )


def test_single_file_counts_are_left_alone() -> None:
    """THE CONTROL THAT KEEPS THIS NARROW. A count over ONE file is stable and
    is the coverage its document is about; flagging those would make the guard
    noise and would delete real information."""
    kept = [
        (f.name, line_no)
        for f in sorted(_DESIGNS.glob("*.md"))
        for line_no, expect, cmd in _steps(f.read_text(encoding="utf-8"))
        if "pytest" in cmd and not _is_wide(cmd) and re.search(r"\b\d+\s+passed\b", expect)
    ]

    assert len(kept) >= 15, (
        f"only {len(kept)} single-file counts remain — this guard was supposed to "
        "leave them alone, so either it widened or the docs lost real coverage"
    )


def test_the_walker_joins_line_continuations() -> None:
    """The false positive this guard already produced once, pinned as a test."""
    steps = _steps(
        "```bash\n"
        "uv run pytest tests/memory/test_one.py \\\n"
        '  -q -k "WIRED"\n'
        "#    expect: 1 passed\n"
        "```\n"
    )

    assert len(steps) == 1, steps
    _line, _expect, cmd = steps[0]
    assert "tests/memory/test_one.py" in cmd, f"the continuation was not joined: {cmd!r}"
    assert not _is_wide(cmd), "a continued single-file command was read as package-wide"


def test_the_guard_sees_a_real_population() -> None:
    """VACUITY CONTROL: the main assertion passes over an empty list by design."""
    wide_total = sum(
        1
        for f in _DESIGNS.glob("*.md")
        for _ln, _exp, cmd in _steps(f.read_text(encoding="utf-8"))
        if _is_wide(cmd)
    )

    assert wide_total >= 10, f"only found {wide_total} package-wide steps"
