#!/usr/bin/env bash
# Run one read-only SQL query against the LIVE database and print the rows.
#
# WHY THIS EXISTS. Two facts about this box were measured, written down, and then
# ignored by ten Verification commands across six design documents:
#
#   * the `sqlite3` CLI is NOT INSTALLED — `command -v sqlite3` finds nothing;
#   * the live database is `<workspace>/stackowl.db`, while `~/.stackowl/stackowl.db`
#     is a ZERO-BYTE stray from 2026-07-25 that still sits there looking canonical.
#
# Both are already recorded in `docs/reference-mapping/PROCESS.md` — inside the section
# called "Evidence, not assertion", as the worked example of a document that would have
# failed its own Verification section. The rule was written in the method document and
# nothing enforced it, so it aged into decoration exactly as an escalation's premise aged
# before `premise_check` and a `partial` stage's evidence aged before `closing_check`.
# This is the fourth instance of that one cure.
#
# THE FAILURE IS THE AMBIGUOUS ZERO, not an error a reader would notice. `sqlite3 ...`
# prints "command not found" to stderr and nothing to stdout; a check written as
# `... | wc -l` then reads 0, and 0 reads as *not yet*. ESC-73's acceptance check is the
# live casualty: `sqlite3 stackowl.db "SELECT name FROM skills WHERE name GLOB ..."` names
# a binary that does not exist AND a relative path that does not exist, and it has sat
# open since 2026-08-31 unable to close whatever the database held.
#
# ONE SOURCE FOR THE PATH. The location comes from `StackowlHome`, never from a literal —
# the same rule `log_since.sh` follows and the same cross-cutting tripwire enforces. A
# script that re-derives ~/.stackowl asks a different instance than the one running.
#
# READ-ONLY BY CONSTRUCTION. Opened with `mode=ro`, so this can never be the thing that
# writes the operator's database. Data changes go through migrations; that is a standing
# rule, and a query helper is exactly where it would get bent.
#
# Usage:  scripts/db_query.sh 'SELECT COUNT(*) FROM skills;'
#         scripts/db_query.sh 'SELECT name FROM owls;' --tsv
# Prints: one row per line, columns separated by a tab. Nothing else, so a caller can
#         pipe it into `wc -l` and mean it.

set -uo pipefail

sql="${1:?usage: db_query.sh '<SQL>'}"

STACKOWL_SQL="$sql" uv run python - <<'PY'
import os
import sqlite3
import sys

from stackowl.paths import StackowlHome

db = StackowlHome.workspace() / "stackowl.db"
if not db.exists() or db.stat().st_size == 0:
    # A MISSING OR EMPTY DATABASE IS AN ERROR, NEVER AN EMPTY RESULT SET. This is the
    # whole point of the script: the stray at ~/.stackowl/stackowl.db is 0 bytes, and a
    # query against it returns nothing at all, which is indistinguishable from a genuinely empty
    # table. Say so on stderr and exit non-zero so a caller cannot read it as a count.
    print(f"db_query: {db} is missing or empty — this is not a zero-row answer",
          file=sys.stderr)
    raise SystemExit(2)

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
try:
    for row in con.execute(os.environ["STACKOWL_SQL"]):
        print("\t".join("" if c is None else str(c) for c in row))
finally:
    con.close()
PY
