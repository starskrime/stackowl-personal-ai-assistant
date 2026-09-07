#!/usr/bin/env bash
# Count log lines matching a pattern, but ONLY from the day a change shipped onward.
#
# WHY THIS EXISTS, and it was found by the mechanism it serves on that mechanism's
# very first real use.
#
# `validate_check.py` re-runs a `closing_check` for every stage recorded `partial`.
# Every log-based check written on 2026-09-06 counted matches across ALL retained
# logs with no lower bound, and on the first run D07.2 reported CLOSEABLE on 4 hits
# of `depth>0 child denied spawn/delegate tool`. All four are dated 2026-08-28. The
# code whose validation they were supposedly evidencing shipped on 2026-09-04, seven
# days LATER (commit 8750d046).
#
# So the check answered "yes, this works in production" using events that predate the
# work entirely. That is precisely the failure the honest-validate rule names — "a fix
# that worked in tests and never fired in production" — reached by a different road: a
# count with no lower bound is satisfied by history.
#
# It is also the denominator rule from CLAUDE.md, one level up. Checking that a number
# is non-zero is not checking what the number is MADE OF, and a raw count over a
# rotating log is made of whatever happens to still be on disk.
#
# TWO INSTRUMENT LESSONS ARE BAKED IN HERE so no individual check has to remember them:
#
#   * `grep -a`. The logs contain null bytes; without it grep treats a file as binary
#     and a real match reports nothing. Measured the same day: a pattern present in
#     src and present in the file counted 0 until -a was added.
#   * A TRUNCATED WINDOW IS ANNOUNCED. If the requested date is older than the oldest
#     retained log, the answer is drawn from a shorter window than asked for, and a 0
#     then means "rotated away", not "never happened". That distinction is the whole
#     subject of this file, so it goes to stderr rather than being silently absorbed.
#
# Usage:  scripts/log_since.sh <YYYY-MM-DD> <grep-pattern>
# Prints: the match count on stdout. Nothing else, so a caller can use it directly.

set -uo pipefail

since="${1:?usage: log_since.sh YYYY-MM-DD <grep-pattern> [exclude-pattern]}"
pattern="${2:?usage: log_since.sh YYYY-MM-DD <grep-pattern> [exclude-pattern]}"
# OPTIONAL THIRD ARGUMENT — lines matching it are subtracted from the count.
#
# WHY IT EXISTS. Some questions are only askable as "X but not Y", and until this
# argument they were not askable at all through this script: "ERROR lines that are NOT
# the known unreachable-provider family" needs a CONJUNCTION, and two calls cannot
# supply one — measured 2026-09-07, the provider patterns match 3,844 lines while ERROR
# matches 3,612, because those patterns also appear at WARNING and INFO. Subtracting one
# count from the other is arithmetic on two different populations.
#
# The gap had teeth: a check that cannot be bounded gets written UNBOUNDED, and the
# guard that forbids that then has to be satisfied cosmetically. A tool that can express
# the bound but not the filter pushes the author toward gaming the gate.
exclude="${3:-}"
# D18.3 — ONE SOURCE FOR THE HOME. This hardcoded the path and the cross-cutting
# tripwire caught it before the commit: a shell script that re-derives ~/.stackowl acts
# on a different instance than the process it is asking about, and no Python guard can
# see it. `full_suite.sh` already asks for exactly this directory; asking again the same
# way is the point. STACKOWL_LOG_DIR remains for tests, which need a window of their own.
if [ -n "${STACKOWL_LOG_DIR:-}" ]; then
    dir="$STACKOWL_LOG_DIR"
else
    dir="$(uv run python -c 'from stackowl.paths import StackowlHome; print(StackowlHome.logs_dir())' 2>/dev/null)"
    if [ -z "$dir" ]; then
        echo "log_since: could not resolve the log directory from StackowlHome" >&2
        echo 0
        exit 2
    fi
fi

if [[ ! "$since" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "log_since: '$since' is not YYYY-MM-DD" >&2
    echo 0
    exit 2
fi

# A FUTURE BOUND IS UNENFORCEABLE, AND SILENTLY SO — refuse it rather than answer it.
#
# The date filter below applies to FILENAMES, and the current file is `stackowl.jsonl`
# with no date in its name, so it is always in the window (deliberately — see below).
# That makes a future bound a lie rather than an empty window: measured 2026-09-06,
# `log_since.sh 2026-09-07 scheduler` returned 5660, the identical count an unbounded
# grep returns, because every one of those lines is in today's file.
#
# So a check bounded at tomorrow reads CLOSEABLE on evidence that PREDATES the fix —
# which is the exact defect the date bound was added to prevent (D07.2 closing on
# events seven days older than the code they evidenced). Found by writing one: a
# closing check was dated 2026-09-07 on 2026-09-06 and would have passed on pre-fix
# lines. Nothing can have shipped tomorrow, so this is always a mistake, and an
# unenforceable bound must fail loudly rather than return a number that looks bounded.
today="$(date -u +%F)"
if [[ "$since" > "$today" ]]; then
    echo "log_since: '$since' is in the FUTURE (today is $today). The bound cannot be" \
         "honoured — the current log file has no date in its name and is always in" \
         "the window, so this would count pre-fix lines as evidence." >&2
    echo 0
    exit 2
fi

shopt -s nullglob
stamped=("$dir"/stackowl-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].jsonl)

# The oldest retained STAMPED log bounds what any question about the past can see.
if [ ${#stamped[@]} -gt 0 ]; then
    oldest=$(basename "${stamped[0]}" .jsonl)
    oldest=${oldest#stackowl-}
    # Lexical compare is correct for ISO dates and needs no date(1).
    if [[ "$oldest" > "$since" ]]; then
        echo "log_since: WINDOW TRUNCATED — asked for $since, oldest retained log is" \
             "$oldest. A zero here may mean 'rotated away', not 'never happened'." >&2
    fi
fi

files=()
for f in "${stamped[@]}"; do
    d=$(basename "$f" .jsonl); d=${d#stackowl-}
    if [[ ! "$d" < "$since" ]]; then
        files+=("$f")
    fi
done
# The CURRENT file carries today's lines and has no date in its name, so it is always
# in the window. Omitting it would make a check blind to evidence produced minutes ago,
# which is the most likely moment for a closing check to finally succeed.
[ -f "$dir/stackowl.jsonl" ] && files+=("$dir/stackowl.jsonl")

if [ ${#files[@]} -eq 0 ]; then
    echo 0
    exit 0
fi

if [ -n "$exclude" ]; then
    grep -ah -- "$pattern" "${files[@]}" 2>/dev/null \
        | grep -av -- "$exclude" | wc -l | tr -d ' '
else
    grep -ah -- "$pattern" "${files[@]}" 2>/dev/null | wc -l | tr -d ' '
fi
