"""How long to wait for a writer's lock is ONE policy, and it was written twice.

MEASURED 2026-09-08. Two modules open the same SQLite file on the same box and
each carried its own lock-wait number:

    src/stackowl/db/pool.py       PRAGMA busy_timeout=15000
    src/stackowl/audit/logger.py  _BUSY_TIMEOUT_MS = 5000

15000 is not an arbitrary number — `pool.py` spends fifteen lines deriving it,
and D11.1 records it as "raised from 5000 after a measured writer-contention
burst where a confirmed-live liveness heartbeat failed both attempts". The
reasoning is explicit: SkillsAssembly's catalogue scan writes ~300 rows from the
OTHER process for 24-40 seconds, so "2 attempts x 5000ms (10s total) was still
shorter than a real observed writer-contention burst".

The audit logger was left on the value that reasoning had just rejected — and it
is strictly worse off than the path that measurement came from, because it makes
ONE attempt with no `retry_once_on_dead_handle` behind it. 5s once, against a
burst known to last 24-40s.

WHAT IT COST, and it is the worst table in the tree to lose a row from:
`[audit] logger.append: INSERT failed` fired twice with
`sqlite3.OperationalError: database is locked` (2026-09-05T15:10:57 and
2026-09-07T07:14:00). `audit_log` is a hash-CHAIN — each row folds the previous
row's hash — so a lost INSERT is not one missing record, it is a gap in the
tamper-evident structure whose whole purpose is to have no gaps.

THE CAUSE IS NOT THE NUMBER, IT IS THAT THERE WERE TWO OF THEM. This is failure
mode #3 from CLAUDE.md — two copies of one rule — and its cure: one source, and
the other asks it. Raising 5000 to 15000 by hand would fix today's symptom and
leave the next person to raise it twice again.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


def test_both_writers_of_this_database_wait_the_same_amount() -> None:
    from stackowl.audit import logger as audit_logger
    from stackowl.db.pool import BUSY_TIMEOUT_MS

    assert audit_logger._BUSY_TIMEOUT_MS == BUSY_TIMEOUT_MS  # noqa: SLF001


def test_the_audit_logger_does_not_define_its_own_number() -> None:
    """It must ASK, not restate — a copy that agrees today drifts tomorrow."""
    src = (_SRC / "stackowl" / "audit" / "logger.py").read_text(encoding="utf-8")
    assert not re.search(r"^_BUSY_TIMEOUT_MS\s*=\s*\d+", src, re.M), (
        "the audit logger has gone back to defining its own lock-wait number"
    )
    assert "from stackowl.db.pool import" in src


@pytest.mark.tripwire
def test_exactly_one_place_in_src_states_the_lock_wait_in_milliseconds() -> None:
    """THE GUARD IS CROSS-CUTTING, so it carries the tripwire marker.

    A third writer of this database is exactly how the first two diverged, and a
    targeted test run for whatever package that writer lives in would never think
    to run a test under `tests/db`.

    It counts NUMERIC literals only. `f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}"`
    is the correct shape and is invisible to this pattern by construction, which
    is what makes the guard describe the rule rather than the current code.

    THE FIRST VERSION OF THIS GUARD WAS VACUOUS AND PASSED ON THE BROKEN TREE.
    It matched ``busy_timeout\\s*=\\s*\\d+``, which sees the pragma string but NOT
    `_BUSY_TIMEOUT_MS = 5000` — the very line that caused the defect — because of
    the `_MS` between the word and the `=`. A guard that cannot see the bug it
    was written for proves nothing; it is checked here by running it against the
    unfixed tree first, where it must report 2.
    """
    # SKIP COMMENTS — read TOKENS, not text. The second version of this guard
    # counted the `#:` docstring above `BUSY_TIMEOUT_MS` in pool.py, which
    # QUOTES the old `_BUSY_TIMEOUT_MS = 5000` line while explaining why it is
    # gone. A guard that a comment can break is the mirror of a guard a comment
    # can satisfy, and this file has now been both.
    offenders: list[str] = []
    pattern = re.compile(r"busy_timeout[_a-z]*\s*=\s*\d+", re.I)
    for path in _SRC.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
            pytest.fail(f"{path} does not tokenize")
        code = "\n".join(
            t.line for t in toks
            if t.type not in (tokenize.COMMENT, tokenize.NL)
        )
        for n, line in enumerate(code.splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_SRC)}: {line.strip()[:70]}")
    offenders = sorted(set(offenders))
    assert len(offenders) == 1, (
        "the lock-wait policy must be stated once and asked for everywhere else; "
        f"found {len(offenders)}:\n  " + "\n  ".join(offenders)
    )
    assert "db/pool.py" in offenders[0], (
        f"the one source must be the pool that owns the database, not {offenders[0]}"
    )
