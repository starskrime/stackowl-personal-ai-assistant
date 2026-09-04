#!/usr/bin/env bash
# Launch the FULL test suite detached and write a stamped log.
#
# It exists because "run the full suite" was a 30-minute act of courage that the
# loop's own instructions forbade ("it hangs on this box" — false, and it cost ten
# red tests). One command, returns immediately, leaves a file to collect.
#
# The full run is the only detector for cross-test pollution and for a retirement
# that left its tests behind. Targeted paths cannot see either.
set -euo pipefail
cd "$(dirname "$0")/.."
log="${STACKOWL_SUITE_LOG:-$HOME/.stackowl/logs/full-suite-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$log")"
nohup uv run pytest -q -p no:cacheprovider >"$log" 2>&1 &
echo "pid $! -> $log"
echo "collect with: tail -5 '$log'"
