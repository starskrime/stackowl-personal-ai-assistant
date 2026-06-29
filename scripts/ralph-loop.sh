#!/usr/bin/env bash
# Ralph Wiggum loop driver (Geoffrey Huntley methodology) for the StackOwl
# persistence/never-give-up arc.
#
# Each iteration spawns a FRESH Claude Code process (clean context window) with
# the existing arc driver prompt. Shared state lives on disk: the plan
# (.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md) tracks which stories are done, so
# every fresh iteration picks up the first unchecked story. The loop stops when
# the agent emits the completion promise — and ONLY then (it is instructed never
# to emit it falsely) — or when MAX_ITER is reached.
#
# Usage:
#   ./scripts/ralph-loop.sh [MAX_ITER]      # default 12
#
# YOLO: runs `claude` with --dangerously-skip-permissions. Sandboxed box only.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

MAX_ITER="${1:-12}"
PROMISE="ARC-A-PERSISTENCE-COMPLETE"
PROMPT_FILE=".ralph/PERSISTENCE_RALPH_PROMPT.md"
PLAN_FILE=".ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md"

for f in "$PROMPT_FILE" "$PLAN_FILE"; do
  [ -f "$f" ] || { echo "FATAL: missing $f" >&2; exit 2; }
done
command -v claude >/dev/null || { echo "FATAL: 'claude' CLI not on PATH" >&2; exit 2; }

mkdir -p logs
TS="$(date +%Y%m%d_%H%M%S)"
SESSION_LOG="logs/ralph_session_${TS}.log"
echo "Ralph loop start: max=$MAX_ITER promise=$PROMISE ts=$TS" | tee "$SESSION_LOG"

for ((i = 1; i <= MAX_ITER; i++)); do
  echo "=== Ralph iteration $i/$MAX_ITER ===" | tee -a "$SESSION_LOG"
  ITER_LOG="logs/ralph_iter_${i}_${TS}.log"

  PROMPT="$(cat "$PROMPT_FILE")

Plan + story status: ${PLAN_FILE} (pick the FIRST unchecked story).
Work ONE story this iteration, verify it (targeted tests + ruff + mypy on changed files only — NEVER full pytest), QA+dev review, commit + push, mark it done in the plan, then STOP.
COMPLETION: output <promise>${PROMISE}</promise> ONLY when EVERY acceptance criterion is unequivocally TRUE (PA0-PA5 done, green, the 2 ratchet gates pass, pushed, server boot-green + census, live never-give-up re-test passed). Do NOT emit it falsely to exit."

  claude -p "$PROMPT" --dangerously-skip-permissions 2>&1 | tee "$ITER_LOG" -a "$SESSION_LOG"

  if grep -qF "<promise>${PROMISE}</promise>" "$ITER_LOG"; then
    echo "✅ Completion promise emitted at iteration $i — arc complete." | tee -a "$SESSION_LOG"
    exit 0
  fi
  echo "--- iteration $i done; promise not yet true; continuing ---" | tee -a "$SESSION_LOG"
done

echo "⏹ Reached MAX_ITER ($MAX_ITER) without the completion promise." | tee -a "$SESSION_LOG"
exit 1
