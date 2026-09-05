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
log="${STACKOWL_SUITE_LOG:-$suite_home/full-suite-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$log")"

# A VERDICT NEEDS A SUBJECT. Measured 2026-09-04: a 30-minute run came back
# `3 failed` and the three failures were about a tree that never existed at any
# single instant — the suite imported synthesizer.py at minute 0 and an item
# edited it at minute 25, so source-reading assertions read one version's line
# numbers against another version's bytes. The verdict was true of nothing.
#
# So the log now names the tree it tested and says whether that tree held still.
# `git stash` is banned here and stopping work for 30 minutes is not the answer:
# the fix is that a run over a moving tree SAYS it was moving, instead of being
# read later as a fact about the current one.
tree_fingerprint() {
  { git rev-parse HEAD 2>/dev/null || echo no-git
    find src tests -name '*.py' -printf '%p %T@ %s\n' 2>/dev/null | sort
  } | md5sum | cut -d' ' -f1
}
start_fp="$(tree_fingerprint)"
start_head="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
start_dirty="$(git status --porcelain 2>/dev/null | wc -l)"

{
  printf 'SUITE START %s  args=%s\n' "$(date -Is)" "${*:-<full suite>}"
  printf 'SUITE TREE  commit=%s dirty_files=%s fingerprint=%s\n' \
    "$start_head" "$start_dirty" "$start_fp"
  # `set +e` so the verdict is written for a RED run too — the failing run is the
  # one whose verdict matters most, and it is exactly the one a bare `&&` drops.
  set +e
  uv run pytest -q -p no:cacheprovider "$@"
  rc=$?
  set -e
  end_fp="$(tree_fingerprint)"
  if [ "$end_fp" != "$start_fp" ]; then
    printf 'SUITE TREE CHANGED DURING THE RUN — start=%s end=%s\n' "$start_fp" "$end_fp"
    printf 'SUITE WARNING this verdict is about NO SINGLE TREE. Source-reading\n'
    printf 'SUITE WARNING assertions (139 call sites) read import-time line numbers\n'
    printf 'SUITE WARNING against edited bytes. Re-run against a still tree.\n'
  fi
  printf 'SUITE DONE %s rc=%s\n' "$(date -Is)" "$rc"
} >"$log" 2>&1 &

ln -sfn "$log" "$(dirname "$log")/full-suite-latest.log"
cat <<MSG
pid $! -> $log
also linked: $(dirname "$log")/full-suite-latest.log

collect with:
  grep -c 'SUITE DONE' "$log"   # 0 = still running, 1 = finished
  tail -6 "$log"                # the summary, any TREE CHANGED warning, and the verdict
  grep '^SUITE TREE' "$log"     # which tree this verdict is actually about
MSG
