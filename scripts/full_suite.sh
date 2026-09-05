#!/usr/bin/env bash
# Launch the test suite DETACHED and write a stamped, self-describing log.
#
# It exists because "run the full suite" was a 30-minute act of courage that the
# loop's own instructions forbade ("it hangs on this box" — false, and it cost ten
# red tests). One command, returns immediately, leaves a file to collect.
#
# The full run is the only detector for cross-test pollution and for a retirement
# that left its tests behind. Targeted paths cannot see either.
#
# The log ENDS WITH ITS OWN VERDICT. Without that, collecting it means inferring
# "finished or still going?" from a process table — and a tail of dots looks
# identical whether the run is at 4% or dead. A log nobody can ask a question of
# is the same write-with-no-reader shape this programme keeps finding.
#
#   ./scripts/full_suite.sh                 # the whole suite
#   ./scripts/full_suite.sh tests/db        # any pytest args, for testing this script
set -euo pipefail
cd "$(dirname "$0")/.."
# D18.3: ask StackowlHome for the log root rather than re-deriving ~/.stackowl.
# A suite run under a non-default STACKOWL_HOME used to write its verdict into the
# DEFAULT home's logs — the run isolated, its record not.
suite_home="$(uv run python -c 'from stackowl.paths import StackowlHome; print(StackowlHome.logs_dir())' 2>/dev/null)"
if [ -z "$suite_home" ]; then
    echo "FATAL: could not resolve the StackOwl log directory from StackowlHome." >&2
    exit 1
fi
# CI PARITY. TZ is the one clause of the reference platform's runner that was not
# vacuous here, and it earned itself immediately: pinning it exposed a scheduler
# helper that resolved "the next local 09:00" against the HOST's zone instead of
# the operator's `system.timezone`. On this box those happen to be the same offset,
# so the bug was invisible; on a UTC server a 09:00 America/Chicago check-in fired
# at 04:00. LANG is pinned alongside it so sort order and text handling do not
# depend on the shell that launched the run.
# PINNED, not defaulted. `${TZ:-UTC}` would leave a developer's own TZ in place,
# which is the opposite of parity: the run would still be host-dependent and the
# scheduler bug above would still have been invisible on this box.
export TZ=UTC
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

log="${STACKOWL_SUITE_LOG:-$suite_home/full-suite-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$log")"

# A VERDICT NEEDS A SUBJECT. Measured 2026-09-04: a 30-minute run came back
# `3 failed` and the three failures were about a tree that never existed at any
# single instant — the suite imported synthesizer.py at minute 0 and an item
# edited it at minute 25, so source-reading assertions read one version's line
# numbers against another version's bytes. The verdict was true of nothing.
#
# THE FIRST FIX WAS THE WRONG ONE AND ITS OWN NUMBERS CONDEMN IT. This script
# was made to LABEL a moving run — "the fix is that a run over a moving tree SAYS
# it was moving" — which repaired the symptom and left the cause alone. Measured
# 2026-09-05 over every log this box has kept: **9 of 17 completed runs, 53%,
# printed SUITE TREE CHANGED**. A detector that disqualifies its own verdict more
# than half the time is not a detector, and the label was only ever read by
# someone who then had to spend another 30 minutes by hand. Nine times, nobody
# did.
#
# So a moving run now RE-RUNS ITSELF, once, against the tree as it then stands.
# The property the programme needs is not a warning, it is a verdict about ONE
# tree — and the loop that edits while the suite runs is not going to stop, so
# the suite has to be the thing that adapts. Bounded at one retry: if the tree
# moves through both attempts the log says so plainly rather than looping.
#
# NOT DONE, and recorded so it is not re-proposed cheaply: running against a
# COPIED snapshot would make the tree immutable by construction, which is
# stronger. It was rejected for now because the import path resolves `stackowl`
# from an editable install, so a snapshot only takes effect by redirecting
# PYTHONPATH — subtly different from what production imports, in the one tool
# this programme trusts most. A wrong verdict from a mis-wired snapshot is worse
# than a late one.
tree_fingerprint() {
  { git rev-parse HEAD 2>/dev/null || echo no-git
    find src tests -name '*.py' -printf '%p %T@ %s\n' 2>/dev/null | sort
  } | md5sum | cut -d' ' -f1
}
start_fp="$(tree_fingerprint)"
start_head="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
start_dirty="$(git status --porcelain 2>/dev/null | wc -l)"

{
  # THE VERDICT IS GUARANTEED, not merely intended. It used to be the last
  # statement inside a `set -euo pipefail` region, so any failure of the
  # fingerprint machinery above it exited the subshell BEFORE the stamp was
  # written — and one kept log proves it happened: full-suite-20260903-235313.log
  # ends with `1 failed, 11856 passed ... in 1767.71s` and NO SUITE DONE line, so
  # the documented collector (`grep -c 'SUITE DONE'`) reports a run that finished
  # 29 hours ago as still going. A log written to answer "did it finish?" that
  # cannot answer it is the same write-with-no-reader shape this script exists to
  # cure. A trap cannot be skipped.
  rc=99
  trap 'printf "SUITE DONE %s rc=%s\n" "$(date -Is)" "$rc"' EXIT

  printf 'SUITE START %s  args=%s\n' "$(date -Is)" "${*:-<full suite>}"
  printf 'SUITE TZ     TZ=%s LANG=%s\n' "$TZ" "$LANG"
  printf 'SUITE TREE  commit=%s dirty_files=%s fingerprint=%s\n' \
    "$start_head" "$start_dirty" "$start_fp"

  attempt=1
  while : ; do
    # `set +e` so the verdict is written for a RED run too — the failing run is
    # the one whose verdict matters most, and it is exactly the one a bare `&&`
    # drops.
    set +e
    uv run pytest -q -p no:cacheprovider "$@"
    rc=$?
    end_fp="$(tree_fingerprint)"
    set -e

    if [ "$end_fp" = "$start_fp" ]; then
      printf 'SUITE TREE STILL — this verdict is about ONE tree (attempt %s)\n' "$attempt"
      break
    fi

    printf 'SUITE TREE CHANGED DURING THE RUN — start=%s end=%s\n' "$start_fp" "$end_fp"
    if [ "$attempt" -ge 2 ]; then
      printf 'SUITE WARNING the tree moved through BOTH attempts. This verdict is\n'
      printf 'SUITE WARNING about NO SINGLE TREE — source-reading assertions read\n'
      printf 'SUITE WARNING import-time line numbers against edited bytes. Re-run\n'
      printf 'SUITE WARNING when editing stops.\n'
      break
    fi
    printf 'SUITE RETRY  re-running once against the tree as it now stands\n'
    attempt=$((attempt + 1))
    start_fp="$end_fp"
  done
} >"$log" 2>&1 &

ln -sfn "$log" "$(dirname "$log")/full-suite-latest.log"
cat <<MSG
pid $! -> $log
also linked: $(dirname "$log")/full-suite-latest.log

collect with:
  grep -c 'SUITE DONE' "$log"        # 0 = still running, 1 = finished (trapped, always written)
  grep -E '^(FAILED|ERROR|SUITE)' "$log"   # failures AND every SUITE line
  grep '^SUITE TREE' "$log"          # which tree this verdict is about, and whether it held still

  # `tail -6` used to be the recipe here and it LIED on exactly the runs that
  # mattered: when the tree-changed warnings fire they push the FAILED lines out
  # of the window, so you learn THAT it failed and never WHAT failed.
MSG
