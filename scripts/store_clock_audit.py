#!/usr/bin/env python3
"""Is every store dated by a column its own writer MOVES?

THE DEFECT THIS EXISTS TO FIND, measured 2026-09-10. `tool_heuristics` was
declared PERIODIC and dated by `created_at`. Its writer — the daily
`tool_outcome_miner` — UPSERTS: 35 rows a day, every one of them taking the
`ON CONFLICT … DO UPDATE SET` branch once the key space saturated on 09-03.
`created_at` is not in that SET, so it stopped moving while the store kept being
written. The health sweep then reported `UNHEALTHY subsystems detected` at ERROR
**149 times in one day** for a writer that runs daily and works.

AND THE SAME DEFECT WAS ALREADY FIXED ONCE, ON THE LINE ABOVE. The registry's
comment on `jobs` records it: "`last_run_at`, NOT `created_at` … MEASURED
2026-09-04: created_at was 1.2 days old while last_run_at was 22 SECONDS old, and
the sweep reported UNHEALTHY 31 times in one boot". That repair was applied to the
one table with the symptom. The QUESTION it answers — *does the writer move the
column the check reads?* — was never asked of the other 53 declarations, so it
came back five weeks later on a different table.

WHY THIS IS DERIVABLE AND NEEDS NO ALLOWLIST. The answer is in the writer's own
SQL. A store written by `INSERT … ON CONFLICT … DO UPDATE SET <cols>` advances
exactly `<cols>` on a repeat write; a store written by a plain INSERT advances
every column it sets, so an insert-time clock is correct. So the rule is:

    a store with a SILENCE WINDOW must be dated by a column its writer moves on
    EVERY write — which for an upsert means the clock is in its DO UPDATE SET.

SCOPED TO WINDOWED DECLARATIONS, AND THAT IS A REAL SCOPE, NOT A CONVENIENCE.
`StoreCadenceContributor` skips any declaration whose `max_silence_days` is None
(`if limit is None or decl.clock is None: continue`), so for ON_DEMAND, SEED,
UNMEASURABLE and RETIRED the clock is never compared to anything and cannot raise
a false alarm. `owls` is the worked example: `created_at` 7.8 days old against an
`updated_at` 0.96 days old, and correct to leave alone, because nothing reads it.

"CANNOT TELL" IS NOT AN EXEMPTION (DEBT-299). A windowed store whose writer this
scan cannot find is reported as UNKNOWN rather than skipped. MEASURED 2026-09-10:
that set is EMPTY — the two windowed stores with no literal `INSERT INTO <table>`
in `src/` (`side_effect_ledger`, `learning_artifacts`) both write through an
`OwnedRepository` subclass declaring `_table = "<name>"`, and that helper builds
`INSERT INTO {table} (...) VALUES (...)` with no `ON CONFLICT` anywhere in the
module, so it is insert-only by construction.

Run it:

    uv run python scripts/store_clock_audit.py
"""

from __future__ import annotations

import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "stackowl"

#: `INSERT INTO <table>` written as a literal. The body is cut at the end of the
#: SQL string or the next INSERT, whichever comes first, so a later statement's
#: `ON CONFLICT` can never be attributed to this one.
_INSERT = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", re.I)
_DO_UPDATE = re.compile(r"ON\s+CONFLICT.*?DO\s+UPDATE\s+SET(.*)", re.I | re.S)
_SET_COL = re.compile(r"(\w+)\s*=")
#: The `OwnedRepository` convention: a subclass names its table once, and the
#: base class's `insert()` builds a plain `INSERT INTO {table}` — never an upsert.
_TABLE_ATTR = re.compile(r'^\s*_table(?:\s*:\s*[^=]+)?\s*=\s*"([a-z_0-9]+)"', re.M)


def _sources(root: pathlib.Path | None = None) -> list[tuple[pathlib.Path, str]]:
    """Every non-migration module under `<root>/src/stackowl`.

    `root` is a PARAMETER rather than a resolved constant on purpose. A sibling
    script computing its own root from `__file__.resolve()` reads the REAL repo
    even when it is executed from a copy — this repo has already paid for that
    once, when a mirror's symlinked `scripts/` made a guard audit a tree nobody
    had edited. Taking the root as an argument also lets a test point the whole
    scan at a synthetic tree, which is the only way to pin the RULE rather than
    today's data.
    """
    src = (root or _ROOT) / "src" / "stackowl"
    out = []
    for p in sorted(src.rglob("*.py")):
        if "migrations" in p.parts:
            continue
        try:
            out.append((p, p.read_text(encoding="utf-8")))
        except (UnicodeDecodeError, OSError):
            continue
    return out


def writer_shapes(root: pathlib.Path | None = None) -> dict[str, set[str]]:
    """table -> the columns a repeat write advances.

    An empty set means a literal INSERT with no `ON CONFLICT` — every column it
    names advances, so an insert-time clock is correct. A table absent from the
    mapping has no literal writer in `src/`.
    """
    shapes: dict[str, set[str]] = {}
    for _path, text in _sources(root):
        for m in _INSERT.finditer(text):
            table = m.group(1).lower()
            rest = text[m.end():]
            # Bound the statement: the SQL literal ends, or the next INSERT begins.
            ends = [i for i in (rest.find('"""'), rest.find("'''")) if i >= 0]
            nxt = _INSERT.search(rest)
            if nxt:
                ends.append(nxt.start())
            body = rest[: min(ends)] if ends else rest
            dm = _DO_UPDATE.search(body)
            cols = set(_SET_COL.findall(dm.group(1))) if dm else set()
            shapes.setdefault(table, set()).update(cols)
    return shapes


def repository_tables(root: pathlib.Path | None = None) -> set[str]:
    """Tables written through an `OwnedRepository` subclass's insert-only helper."""
    out: set[str] = set()
    for _path, text in _sources(root):
        out.update(_TABLE_ATTR.findall(text))
    return out


def audit(
    root: pathlib.Path | None = None,
    declarations: object | None = None,
) -> dict[str, list[tuple[str, str, list[str]]]]:
    """Classify every WINDOWED declaration. Keys: moved, insert_only, not_moved, unknown."""
    if declarations is None:
        sys.path.insert(0, str(_ROOT / "src"))
        from stackowl.health.store_cadence import DECLARATIONS as declarations  # noqa: N813

    shapes = writer_shapes(root)
    repos = repository_tables(root)
    out: dict[str, list[tuple[str, str, list[str]]]] = {
        "moved": [], "insert_only": [], "not_moved": [], "unknown": [],
    }
    for decl in declarations:  # type: ignore[union-attr]
        if decl.cadence.max_silence_days is None or decl.clock is None:
            continue  # never compared; see the module docstring
        if decl.table not in shapes:
            if decl.table in repos:
                out["insert_only"].append((decl.table, decl.clock, ["OwnedRepository"]))
            else:
                out["unknown"].append((decl.table, decl.clock, []))
            continue
        cols = shapes[decl.table]
        if not cols:
            out["insert_only"].append((decl.table, decl.clock, []))
        elif decl.clock in cols:
            out["moved"].append((decl.table, decl.clock, sorted(cols)))
        else:
            out["not_moved"].append((decl.table, decl.clock, sorted(cols)))
    return out


def main() -> int:
    res = audit()
    total = sum(len(v) for v in res.values())
    print(f"WINDOWED STORES (HOT + PERIODIC): {total} — the only ones whose clock is ever read\n")
    for table, clock, cols in sorted(res["not_moved"]):
        print(f"  CLOCK NOT MOVED  {table:<24} clock={clock:<14} the upsert advances {cols}")
    if res["not_moved"]:
        print()
    for table, clock, _ in sorted(res["unknown"]):
        print(f"  WRITER UNKNOWN   {table:<24} clock={clock} — cannot tell, which is NOT an exemption")
    if res["unknown"]:
        print()
    print(f"  clock moved by its own upsert : {len(res['moved'])}")
    print(f"  insert-only (clock is correct): {len(res['insert_only'])}")
    print(f"  CLOCK NOT MOVED               : {len(res['not_moved'])}")
    print(f"  writer unknown                : {len(res['unknown'])}")
    print(
        "\nA store in the last two groups goes silent while its writer runs, and the "
        "health sweep reports it at ERROR. `tool_heuristics` did exactly that 149 times "
        "on 2026-09-10."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
