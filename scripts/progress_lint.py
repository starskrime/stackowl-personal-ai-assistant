#!/usr/bin/env python3
"""Is progress.yml actually recording what it looks like it records?

WHY THIS EXISTS. On 2026-08-08 a full slice of D10.2 work was written into that
item's ``changes:`` list — and vanished. The item carried the key TWICE, once
near the top and once after ``decisions:``, both empty. YAML keeps the last
occurrence, so the write landed in the copy that was immediately overwritten by
the empty one. ``yaml.safe_load`` reported zero changes with no error, no
warning, and no indication that anything had been discarded.

Seven items were in that state. All fourteen keys happened to be empty, so
nothing had been lost yet — but the file's whole job is to be the state of
record, and a state of record that silently drops writes is worse than one that
is merely out of date, because it looks current.

This is the same shape as every other defect this programme keeps finding: the
write happens, the effect does not, and nothing says so. So it becomes a check.

USAGE

    uv run python scripts/progress_lint.py

Exit status is 1 when something is wrong, 0 when the file is sound.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

import yaml

#: Keys that legitimately repeat INSIDE an item's nested structures (a list of
#: sub-items each with a name/status). Only top-level item keys are checked, but
#: the flat regex cannot tell nesting apart, so these are excluded by name.
_NESTED_OK = frozenset({"items", "name", "detail", "status", "decision"})

_STAGES = (
    "brainstorm", "architect", "implement", "cleanup", "test", "validate", "document",
)
#: ``no_change_needed`` is PROCESS.md's stated legitimate outcome; ``blocked``
#: is in use for a stage waiting on something outside the programme (D05.8's
#: architect stage needs live interactive traffic). Both are real states, not
#: typos — the linter's job is to catch a stage value nobody meant to write,
#: which means the vocabulary has to match what the process actually uses.
#: ``pre_process_exception`` records a stage that never ran because the item
#: PREDATES the process that requires it. D01.6 was the first item worked and was
#: built on 2026-07-25, the same day the grill rule itself was written; its
#: brainstorm never happened. ``done`` would be a lie and ``no_change_needed``
#: claims the stage examined the item and found nothing to change, which is also
#: not what occurred. Bakir accepted it as a documented exception on 2026-08-15,
#: so the vocabulary gains a word for it rather than an existing word being bent
#: to cover a case it does not mean.
_VALID_STAGE_VALUES = frozenset({
    "done", "partial", "not_started", "no_change_needed", "blocked",
    "pre_process_exception",
})


def _item_blocks(text: str) -> list[tuple[str, str]]:
    """(id, raw block) for every item, by source position.

    Deliberately textual. The whole point is to see what ``yaml.safe_load``
    HIDES, so parsing first and inspecting the result would miss it.
    """
    starts = [m.start() for m in re.finditer(r"^  - id: ", text, re.M)]
    out: list[tuple[str, str]] = []
    for start, end in zip(starts, [*starts[1:], len(text)], strict=True):
        block = text[start:end]
        match = re.search(r"id: (\S+)", block)
        out.append((match.group(1) if match else "<unknown>", block))
    return out


def main() -> int:
    path = Path(__file__).resolve().parent.parent / "progress.yml"
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    for ident, block in _item_blocks(text):
        keys = re.findall(r"^    (\w+):", block, re.M)
        dupes = {
            k: n for k, n in collections.Counter(keys).items()
            if n > 1 and k not in _NESTED_OK
        }
        for key, n in sorted(dupes.items()):
            problems.append(
                f"{ident}: '{key}' appears {n} times — YAML keeps the LAST, so a "
                f"write to any earlier copy is discarded without an error"
            )

    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — the message is the output
        print(f"✗ progress.yml does not parse: {exc}")
        return 1

    for item in data.get("items", []):
        ident = item.get("id", "<unknown>")
        stages = item.get("stages") or {}
        missing = [s for s in _STAGES if s not in stages]
        if missing and stages:
            problems.append(f"{ident}: stages missing {', '.join(missing)}")
        for stage, value in stages.items():
            if value not in _VALID_STAGE_VALUES:
                problems.append(
                    f"{ident}: stage '{stage}' is {value!r}, not one of "
                    f"{sorted(_VALID_STAGE_VALUES)}"
                )

    if problems:
        print(f"✗ {len(problems)} problem(s) in progress.yml:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"✓ progress.yml sound — {len(data.get('items', []))} items, no duplicate keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
