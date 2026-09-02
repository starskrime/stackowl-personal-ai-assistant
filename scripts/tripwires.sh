#!/usr/bin/env bash
# The gate that must pass before ANY commit, whatever the change touched.
#
# WHY THIS EXISTS. The item loop's `test` stage says "targeted paths with
# timeouts", and paths get picked by what the item touched. A cross-cutting
# guard — one that protects a property of the WHOLE repo — never looks related to
# anything, so it is never selected. Two real defects shipped that way, both
# caught later by accident:
#
#   c80b5f85  usage_report.py queried the owner-governed `task_outcomes` with no
#             owner_id predicate. A cross-tenant read. That item ran
#             tests/tools/meta and tests/startup; the tripwire lives in
#             tests/tenancy, so it was never run.
#   95a841c3  deleted six modules and left three of their entries in the
#             owner-scope allowlist, so the allowlist claimed gaps that no longer
#             existed. "Retired means deleted" should have covered the allowlist
#             rows in that same commit.
#
# A rule that says "remember to run the tripwires" is the same rule that already
# failed twice. This is the executable version.
#
# FAST BY DESIGN — the whole gate is well under a minute, because a gate that is
# slow is a gate that gets skipped. Anything marked `@pytest.mark.tripwire`
# joins automatically: the marker is the single source, so a new guard opts in
# where it lives instead of being added to a list here that would drift.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

step() { printf '\n=== %s\n' "$1"; }

step "cross-cutting guards (pytest -m tripwire)"
uv run pytest -m tripwire -q -p no:cacheprovider 2>&1 | tail -2 || fail=1

step "progress.yml is parseable and has no duplicate keys"
uv run python scripts/progress_lint.py 2>&1 | tail -1 || fail=1

step "ruff baseline (must not rise above 35)"
n=$(uv run ruff check src/ --output-format=concise 2>/dev/null | grep -c ':')
echo "  ruff findings: $n"
[ "$n" -le 35 ] || { echo "  RUFF BASELINE ROSE"; fail=1; }

step "mypy baseline (must not rise above 65)"
# NOT a pipeline: `set -o pipefail` plus mypy's non-zero exit (it exits 1 whenever
# it reports anything) made the `|| echo 0` fallback fire even when grep had
# already matched, so the count came out as "65\n0" and the comparison errored.
# The gate caught a defect in itself on its first run, which is the right way
# round.
mypy_out=$(uv run mypy src/stackowl 2>&1 | tail -1)
m=$(printf '%s' "$mypy_out" | grep -oP '\d+(?= errors)')
m=${m:-0}
echo "  mypy errors: $m"
[ "$m" -le 65 ] || { echo "  MYPY BASELINE ROSE"; fail=1; }

printf '\n'
if [ "$fail" -eq 0 ]; then echo "TRIPWIRES PASS"; else echo "TRIPWIRES FAILED — do not commit"; fi
exit "$fail"
