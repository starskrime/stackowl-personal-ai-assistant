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
log="${STACKOWL_SUITE_LOG:-$HOME/.stackowl/logs/full-suite-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$log")"

{
  printf 'SUITE START %s  args=%s\n' "$(date -Is)" "${*:-<full suite>}"
  # `set +e` so the verdict is written for a RED run too — the failing run is the
  # one whose verdict matters most, and it is exactly the one a bare `&&` drops.
  set +e
  uv run pytest -q -p no:cacheprovider "$@"
  rc=$?
  set -e
  printf 'SUITE DONE %s rc=%s\n' "$(date -Is)" "$rc"
} >"$log" 2>&1 &

ln -sfn "$log" "$(dirname "$log")/full-suite-latest.log"
cat <<MSG
pid $! -> $log
also linked: $(dirname "$log")/full-suite-latest.log

collect with:
  grep -c 'SUITE DONE' "$log"   # 0 = still running, 1 = finished
  tail -3 "$log"                # the summary line and the verdict
MSG
