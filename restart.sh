#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🦉 StackOwl — Restart Script
#
# Reads the PID from session.tmp, kills the running process,
# then starts start.sh which resumes from the saved session
# without asking any questions.
#
# Usage: ./restart.sh
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION_FILE="$SCRIPT_DIR/session.tmp"

# ─── Colors ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

log_info()  { echo -e "${GREEN}✓${RESET} $1"; }
log_warn()  { echo -e "${YELLOW}⚠${RESET} $1"; }
log_error() { echo -e "${RED}✗${RESET} $1"; }
log_step()  { echo -e "${CYAN}▸${RESET} ${BOLD}$1${RESET}"; }
log_dim()   { echo -e "${DIM}  $1${RESET}"; }

# ─── Read PID from session.tmp ───────────────────────────────────

if [ ! -f "$SESSION_FILE" ]; then
  log_warn "No session.tmp found — nothing to restart."
  log_dim  "Run ./start.sh to start StackOwl for the first time."
  exit 0
fi

SAVED_PID=$(node -e "
  try {
    const c = JSON.parse(require('fs').readFileSync('$SESSION_FILE', 'utf8'));
    console.log(c.pid || '');
  } catch { console.log(''); }
" 2>/dev/null)

SAVED_MODE=$(node -e "
  try {
    const c = JSON.parse(require('fs').readFileSync('$SESSION_FILE', 'utf8'));
    console.log(c.launchMode || '');
  } catch { console.log(''); }
" 2>/dev/null)

# ─── Kill existing process ────────────────────────────────────────

echo ""
log_step "Restarting StackOwl..."
log_dim  "Saved mode: ${SAVED_MODE:-unknown}"

if [ -n "$SAVED_PID" ] && [ "$SAVED_PID" != "0" ]; then
  # Kill the process group so child processes (tsx, node) also die
  if kill -0 "$SAVED_PID" 2>/dev/null; then
    log_info "Stopping process PID $SAVED_PID..."
    # Try SIGTERM first, then SIGKILL if needed
    kill -TERM "$SAVED_PID" 2>/dev/null || true
    sleep 1
    if kill -0 "$SAVED_PID" 2>/dev/null; then
      log_warn "Process still running, sending SIGKILL..."
      kill -KILL "$SAVED_PID" 2>/dev/null || true
      sleep 1
    fi
    log_info "Process stopped."
  else
    log_warn "PID $SAVED_PID is not running (may have already stopped)."
  fi

  # Also kill any orphaned tsx/node processes running index.ts from this directory
  pkill -f "tsx src/index.ts" 2>/dev/null || true
  pkill -f "node.*stackowl" 2>/dev/null || true
else
  log_warn "No PID in session.tmp — attempting to kill by process name..."
  pkill -f "tsx src/index.ts" 2>/dev/null || true
fi

echo ""
log_step "Starting fresh..."
echo ""

# start.sh will find session.tmp and resume without asking questions
exec "$SCRIPT_DIR/start.sh"
