#!/usr/bin/env python3
"""Which REFERENCE_MAP entries describe OUR side using symbols that are gone?

WHY THIS EXISTS. The map is the programme's scheduling input: items are chosen,
sequenced and sized from it. Its "**StackOwl.**" line is a 2026-07 snapshot of our
own tree, and in one week TWO entries were found stale by accident, each while
working the item it described — D12.3 named a per-channel `memory_callbacks.py`
that does not exist, and D12.5/D02.3 described a `serialize_prior` gate that was
deleted. Scheduling work on a stale premise is the same defect as the "the full
suite hangs" instruction: the document is obeyed, the tree has moved on, and
nobody notices because nobody re-reads the document against the code.

    uv run python scripts/map_freshness.py

Exit status is always 0. THIS IS A REPORT, NOT A GATE, and that is deliberate.

WHAT IT CANNOT DO, measured rather than guessed:

* **Assertion vs claim is handled, but only by keyword.** D14.1 says
  `CommandDef`-style flags "are not present" — perfectly correct — and a pure
  existence check flags it exactly like a stale claim. That is the same
  unsound-predicate shape that killed a drop-table tripwire earlier in this
  programme after four attempts. Here the sentence around the token is checked for
  a negation ("not present", "GONE", "no longer exists", …) and skipped if it has
  one. That is a HEURISTIC over prose: a differently-worded denial will still be
  flagged, which is why this stays a report a human triages rather than a gate.
* **It only sees `backticked` tokens** — 94 of them across 110 entries. Most
  entries describe our side in prose, which nothing here can check.
* **A "definition" is approximated** by a def/class/assignment or a matching
  filename. Module names imported elsewhere (`inflight_router`, `git_tool`,
  `tool_presets`) are resolved separately, because a first version of this script
  called all three missing when all three exist.

* **One known false positive, named so nobody re-investigates it:** D11.2 writes
  the SQL keyword `LIKE` in backticks. It is prose about SQL, not a symbol. It is
  left rather than special-cased, because a keyword list is the kind of thing that
  grows quietly and then has to be maintained; one documented false positive in a
  report a human reads is cheaper and more honest.

So the ABSENT list below is the only high-confidence signal: a backticked token
with no definition, no module, no SQL table and no quoted string anywhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MAP = _ROOT / "docs" / "reference-mapping" / "REFERENCE_MAP.md"
_SRC = _ROOT / "src" / "stackowl"
_IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*(?:\.py)?)`")
#: An entry that SAYS a thing is absent is correct, not stale. Without this the
#: report is 100% false positives the moment a stale entry is fixed, because the
#: fix keeps the name and adds the denial.
_DENIAL = re.compile(
    r"\b(is|are|was|were)\s+(GONE|gone)\b|no longer exist|does not exist|do not exist|"
    r"not present|never existed|has been (removed|deleted)|is (removed|deleted)",
    re.I,
)
#: Below this length a token is too generic to grep for without noise.
_MIN_LEN = 4


def _grep(pattern: str, path: Path) -> bool:
    return subprocess.run(
        ["grep", "-rqE", pattern, str(path)], capture_output=True, check=False
    ).returncode == 0


def is_absent(token: str) -> bool:
    """True only when the token has no definition, module, table or string form."""
    if token.endswith(".py"):
        return not any(_SRC.rglob(token))
    base = re.escape(token.split(".")[-1])
    if len(base) < _MIN_LEN:
        return False
    if any(_SRC.rglob(f"{token.split('.')[-1]}.py")):
        return False  # a module, which never appears as a def
    for pattern in (
        rf"^\s*(def|async def|class)\s+{base}\b",   # python definition
        rf"^\s*{base}\s*[:=]",                      # module-level binding
        rf"(CREATE TABLE|CREATE VIRTUAL TABLE)[^;]*\b{base}\b",  # sql table
        rf"[\"']{base}[\"']",                       # a name used as a string
    ):
        if _grep(pattern, _SRC):
            return False
    return True


def _denied_nearby(body: str, token: str) -> bool:
    """Does the sentence carrying ``token`` say the thing is absent?"""
    for sentence in re.split(r"(?<=[.;])\s+", body):
        if f"`{token}`" in sentence and _DENIAL.search(sentence):
            return True
    return False


def stale_entries() -> list[tuple[str, list[str]]]:
    """Map entries whose StackOwl line names an absent symbol."""
    text = _MAP.read_text(encoding="utf-8")
    out: list[tuple[str, list[str]]] = []
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        ident = block.split("·")[0].strip().split()[0]
        match = re.search(
            r"^\*\*StackOwl\.\*\*(.*?)(?=^\*\*|\Z)", block, flags=re.M | re.S
        )
        if not match:
            continue
        body = match.group(1)
        missing = sorted({
            t for t in _IDENT.findall(body)
            if is_absent(t) and not _denied_nearby(body, t)
        })
        if missing:
            out.append((ident, missing))
    return out


def main() -> int:
    rows = stale_entries()
    if not rows:
        print("✓ every backticked symbol in the map's StackOwl lines still exists")
        return 0
    print(f"{len(rows)} map entr(y/ies) name a symbol that is not in src/ — TRIAGE, not a verdict:\n")
    for ident, missing in rows:
        print(f"  {ident:8s} -> {', '.join(missing)}")
    print("\nSentences that DENY a symbol's existence are already skipped; anything")
    print("listed here reads as a claim that the thing is present. Verify before acting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
