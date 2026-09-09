"""Which design documents declare the source files you just changed.

WHY THIS EXISTS, and it is the same argument `tests_touching.py` was built on.

CLAUDE.md and the item-loop skill both say the document is the FIFTH SURFACE of a
change, and say it in the strongest terms available — `d8b8ba81` retired the
tool-loop hard stops across code, config, tests and a module docstring, and D05.7
advertised the deleted `hard_stop_enabled` flag for eight days afterwards. That is a
warning, and a warning names the HAZARD while the reader needs the ANSWER. The
answer to "which tests does this change touch" has been derivable since DEBT-231.
The answer to "which DOCUMENTS does this change touch" has only ever been available
AFTER the fact, from `doc_check.py`'s STALE report — which is to say, on a later
loop, as somebody else's clean-up.

MEASURED 2026-09-09 over the last 60 commits: **15 of them touched a path some
design document declares as its `Source`.** One commit in four. `bf603ef7` reached
NINE documents and `c82dac21` seven; two whole loops (DEBT-242, DEBT-254) were spent
draining the staleness that earlier loops created, and DEBT-254's own record says the
eight documents it drained "were made stale by MY OWN commits in this session".

The cost is not the re-reading — that judgement is real work and cannot be
automated. The cost is that it happens in a DIFFERENT loop from the change, by which
time the author no longer remembers whether the change concerned the document.

Usage:  uv run python scripts/docs_touching.py [path ...]
        (no arguments = whatever your working tree has changed)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

# ONE SOURCE FOR EVERY PATH RULE. `doc_check` already parses the header genre this
# corpus actually uses — `Source (new):`, `Source (changed):`, package-relative
# citations, `module.py::Symbol` — and each of those was a correction it paid for.
# Re-deriving any of it here would be the "two copies of one rule" shape, and the
# copy that drifts is always the one nobody runs.
from doc_check import (  # noqa: E402
    _CITATION,
    _header,
    _resolve,
    _source_fields,
    cites,
)

_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


def _changed_files() -> list[str]:
    """The working tree's changes, tracked and untracked.

    Mirrors `tests_touching.py` deliberately: two tools that answer "what did I
    change" differently would disagree exactly when it mattered.
    """
    out: list[str] = []
    for cmd in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=_ROOT)
            out.extend(line.strip() for line in res.stdout.splitlines() if line.strip())
        except Exception as exc:  # pragma: no cover — git absent
            print(f"  (could not read changes: {exc})", file=sys.stderr)
    return sorted(set(out))


def citation_index() -> dict[str, list[tuple[str, str]]]:
    """cited path -> [(document, its `Last verified` line)], across the design set."""
    index: dict[str, list[tuple[str, str]]] = {}
    for doc in sorted(_DESIGNS.glob("*.md")):
        head = _header(doc.read_text(encoding="utf-8"))
        source = _source_fields(head)
        verified = head.get("Last verified", "") or "(no verification date)"
        for raw in re.findall(_CITATION, source):
            if "/" not in raw:
                continue
            resolved = _resolve(raw)
            if resolved:
                index.setdefault(resolved, []).append((doc.name, verified))
    return index


def documents_touching(changed: list[str]) -> dict[str, list[tuple[str, str]]]:
    """changed file -> the documents that declare it, directory citations included."""
    index = citation_index()
    hits: dict[str, list[tuple[str, str]]] = {}
    for path in changed:
        found = [
            entry
            for cited, entries in index.items()
            if cites(path, cited)
            for entry in entries
        ]
        if found:
            hits[path] = sorted(set(found))
    return hits


def main() -> int:
    changed = sys.argv[1:] or _changed_files()
    if not changed:
        print("nothing changed — no documents to re-read")
        return 0

    hits = documents_touching(changed)
    if not hits:
        print(
            f"{len(changed)} changed path(s); NONE is declared as the `Source` of any "
            "design document."
        )
        # NOT the same as "no document is affected", and saying so is the point: 35 of
        # 84 documents declare no resolvable Source at all, so this tool is silent
        # about them by construction. A denominator a reader cannot see is the error
        # this repo pays for most.
        print(
            "  (This reads declared `Source:` headers only. 35 of 84 documents declare "
            "none, and no tool can speak for those.)"
        )
        return 0

    for path in sorted(hits):
        print(f"\n{path}  ({len(hits[path])} document(s))")
        for doc, verified in hits[path]:
            print(f"    {doc:16} last verified: {verified[:96]}")

    every = sorted({doc for entries in hits.values() for doc, _v in entries})
    print(
        f"\n{len(every)} design document(s) declare what you changed. Re-read each one "
        "BEFORE committing: either it is genuinely affected — correct it in the SAME "
        "change — or it is not, and it wants a `Reviewed:` sha so the report stops "
        "asking."
    )
    for doc in every:
        print(f"  docs/reference-mapping/designs/{doc}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
