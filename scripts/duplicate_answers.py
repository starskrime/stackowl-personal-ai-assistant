#!/usr/bin/env python3
"""Identical answers delivered twice inside a short window.

WHY A SCRIPT AND NOT A ONE-LINER. This question is a GROUPED one — repeats of one
hash within a window — and this repo has already paid for the difference: the
commonest message hash recurs 4,472 times across 71 days and is a recurring
scheduled message, so a plain duplicate COUNT calls correct behaviour a defect.
"Count incidents, not log lines", applied to deliveries.

It reads `notification_log`, not the log files, because a duplicate delivery is a
DATABASE fact — the row is written by the router whether or not anything logged.

    uv run python scripts/duplicate_answers.py                # whole retained window
    uv run python scripts/duplicate_answers.py --since 2026-09-10
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

from stackowl.paths import StackowlHome

#: Two deliveries of identical text further apart than this are a RECURRENCE, not
#: a duplicate. Sized from the measured population: every one of the 16 observed
#: duplicates fell inside 112s, and the nearest legitimate repeat of any hash is
#: hours away.
_WINDOW_SECONDS = 120.0

#: The categories that are an ANSWER to somebody. `canary` repeats by design and
#: `job_failed` is a status, so counting them would drown the signal.
_ANSWER_CATEGORIES = ("turn_answer", "goal_answer")


def duplicates(since: str | None = None) -> list[tuple[str, str, float]]:
    """``[(hash, when, gap_seconds)]`` for answers repeated inside the window."""
    db = StackowlHome.workspace() / "stackowl.db"
    if not db.exists() or db.stat().st_size == 0:
        print(f"duplicate_answers: {db} is missing or empty", file=sys.stderr)
        raise SystemExit(2)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    marks = ",".join("?" for _ in _ANSWER_CATEGORIES)
    sql = (
        "SELECT message_hash, created_at FROM notification_log "  # noqa: S608
        f"WHERE category IN ({marks}) "
        "AND message_hash IS NOT NULL AND message_hash != ''"
    )
    params: list[object] = list(_ANSWER_CATEGORIES)
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    grouped: dict[str, list[datetime]] = defaultdict(list)
    for digest, when in conn.execute(sql + " ORDER BY message_hash, created_at", params):
        try:
            grouped[digest].append(datetime.fromisoformat(when))
        except ValueError:
            continue
    out: list[tuple[str, str, float]] = []
    for digest, stamps in grouped.items():
        for first, second in zip(stamps, stamps[1:], strict=False):
            gap = (second - first).total_seconds()
            if gap < _WINDOW_SECONDS:
                out.append((digest, first.isoformat(), gap))
    return sorted(out, key=lambda row: row[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="ISO date lower bound")
    args = parser.parse_args(argv)
    found = duplicates(args.since)
    for digest, when, gap in found:
        print(f"{when}  gap={gap:6.1f}s  {digest}")
    # LAST LINE IS THE COUNT, so a caller can read it without parsing the rest.
    print(len(found))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
