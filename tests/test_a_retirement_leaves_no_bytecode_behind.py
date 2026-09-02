"""A deleted module must not leave its compiled bytecode in the tree.

WHY THIS EXISTS. Bakir's standing rule (2026-09-01) is that whatever is retired
is DELETED — code, registration, tests and rows, in the same change. Deleting the
``.py`` does not delete ``__pycache__/<name>.cpython-XXX.pyc``, so the tree kept
231 compiled modules whose source had been removed, some of them across an
interpreter version the project no longer uses.

WHAT IT ACTUALLY COST, measured 2026-09-02. Orphaned bytecode cannot be imported
(CPython will not load a sourceless module out of ``__pycache__``), so this was
never a correctness bug in the running platform. It was worse than that: it was
an INSTRUMENT bug. Almost every measurement this programme makes is a recursive
grep over ``src/``, and grep reads ``.pyc`` files. Two separate readings were
wrong in one sitting because of it:

  * an escalation premise-check asking "is the fact-entity feeder gone?" answered
    EXPIRED — the string ``kuzu_sync`` was still in the tree, inside
    ``scheduler/handlers/__pycache__/dream_worker.cpython-311.pyc``, months after
    both modules were deleted. That verdict would have closed a live question.
  * a count of graph feeders reported 7 where the real number was 2; the rest
    were caches.

This is the same family as CLAUDE.md's `"msg": "` space and the SQL ``LIKE``
underscore: the pattern matched something other than the thing, and the number
looked authoritative anyway.

THE RULE. A ``__pycache__`` entry whose source no longer exists is residue of a
retirement. Delete it with the module.

    find src tests -name '__pycache__' -type d -prune -exec rm -rf {} +
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_ROOTS = ("src", "tests", "scripts")


def _orphaned() -> list[pathlib.Path]:
    """Every cached module in the tree whose source file is gone."""
    out: list[pathlib.Path] = []
    for name in _ROOTS:
        root = _ROOT / name
        if not root.is_dir():
            continue
        for pyc in root.rglob("__pycache__/*.pyc"):
            # "mod.cpython-314.pyc" -> "mod.py", one directory up.
            source = pyc.parent.parent / f"{pyc.name.split('.')[0]}.py"
            if not source.exists():
                out.append(pyc.relative_to(_ROOT))
    return out


@pytest.mark.tripwire
def test_no_bytecode_outlives_its_source() -> None:
    orphans = _orphaned()
    assert not orphans, (
        f"{len(orphans)} compiled modules have no source — residue of a "
        "retirement, and every recursive grep over the tree reads them:\n  "
        + "\n  ".join(str(p) for p in sorted(orphans)[:20])
        + "\nRemove them: find src tests -name __pycache__ -type d -prune -exec rm -rf {} +"
    )
