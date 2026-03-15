#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🦉 StackOwl — Start Script
#
# Config (provider, keys, tokens) → stackowl.config.json  (never touched here)
# Launch mode (cli/telegram/all)   → session.tmp           (remembered between runs)
#
# First run:  asks only "how to start?" → saves to session.tmp
# Next runs:  reads session.tmp → starts immediately, no questions
# Reset mode: delete session.tmp to be asked again
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/stackowl.config.json"
SESSION_FILE="$SCRIPT_DIR/session.tmp"

LAUNCH_MODE=""

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

# ─── Banner ─────────────────────────────────────────────────────

print_banner() {
  echo ""
  echo -e "${YELLOW}   _____    __                      __      ____                 __  ${RESET}"
  echo -e "${YELLOW}  / ___/   / /_   ____ _   _____   / /__   / __ \\   _      __   / /  ${RESET}"
  echo -e "${YELLOW}  \\__ \\   / __/  / __ \`/  / ___/  / //_/  / / / /  | | /| / /  / /  ${RESET}"
  echo -e "${YELLOW} ___/ /  / /_   / /_/ /  / /__   / ,<    / /_/ /   | |/ |/ /  / /   ${RESET}"
  echo -e "${YELLOW}/____/   \\__/   \\__,_/   \\___/  /_/|_|   \\____/    |__/|__/  /_/    ${RESET}"
  echo -e "${DIM}──────────────────────────────────────────────────────────────────────${RESET}"
  echo -e "${DIM}🦉 Personal AI Assistant • Challenge Everything${RESET}"
  echo -e "${DIM}──────────────────────────────────────────────────────────────────────${RESET}"
  echo ""
}

# ─── Helpers ────────────────────────────────────────────────────

json_read() {
  # json_read <file> <key>  — safely reads a top-level string value from a JSON file
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$1', 'utf8'));
      console.log(c['$2'] || '');
    } catch { console.log(''); }
  " 2>/dev/null
}

has_telegram() {
  # Returns 0 (true) if stackowl.config.json has telegram.enabled = true
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      process.exit(c.telegram && c.telegram.enabled ? 0 : 1);
    } catch { process.exit(1); }
  " 2>/dev/null
}

# ─── Session ────────────────────────────────────────────────────

save_session() {
  node -e "
    const fs = require('fs');
    const data = { launchMode: '$LAUNCH_MODE', savedAt: new Date().toISOString() };
    fs.writeFileSync('$SESSION_FILE', JSON.stringify(data, null, 2));
  "
  log_info "Saved launch mode to session.tmp  (delete this file to change it)"
}

write_pid_to_session() {
  local pid="$1"
  node -e "
    const fs = require('fs');
    let data = {};
    try { data = JSON.parse(fs.readFileSync('$SESSION_FILE', 'utf8')); } catch {}
    data.pid = $pid;
    data.startedAt = new Date().toISOString();
    fs.writeFileSync('$SESSION_FILE', JSON.stringify(data, null, 2));
  " 2>/dev/null || true
}

# ─── Prerequisites ───────────────────────────────────────────────

check_prerequisites() {
  if ! command -v node &> /dev/null; then
    log_error "Node.js is not installed. Please install Node.js >= 22."
    exit 1
  fi

  NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_VERSION" -lt 22 ]; then
    log_error "Node.js >= 22 required (found v$(node -v))"
    exit 1
  fi

  if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
    log_warn "Dependencies not installed. Running npm install..."
    (cd "$SCRIPT_DIR" && npm install)
  fi

  if [ ! -f "$CONFIG_FILE" ]; then
    log_error "stackowl.config.json not found at $CONFIG_FILE"
    log_error "Create it manually or copy from stackowl.config.example.json"
    exit 1
  fi

  # ── Python & Scrapling (anti-bot web scraping) ──
  install_scrapling
}

install_scrapling() {
  if ! command -v python3 &> /dev/null; then
    log_warn "Python 3 not found — scrapling_fetch tool will be unavailable."
    log_dim  "Install Python 3.8+ to enable anti-bot web scraping."
    return
  fi

  # Check if scrapling is already installed
  if python3 -c "import scrapling" 2>/dev/null; then
    log_info "Scrapling already installed"
  else
    log_step "Installing Scrapling (anti-bot web scraping)..."
    pip install "scrapling[all]" 2>&1 | tail -3
    if python3 -c "import scrapling" 2>/dev/null; then
      log_info "Scrapling installed successfully"
    else
      log_warn "Scrapling installation failed — scrapling_fetch tool will be unavailable."
      log_dim  "Try manually: pip install scrapling[all]"
      return
    fi
  fi

  # Check for missing dependencies that scrapling needs
  local MISSING_DEPS=""

  python3 -c "import curl_cffi" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS curl_cffi"
  python3 -c "import browserforge" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS browserforge"
  python3 -c "import playwright" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS playwright"
  python3 -c "import patchright" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS patchright"
  python3 -c "import msgspec" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS msgspec"

  if [ -n "$MISSING_DEPS" ]; then
    log_step "Installing Scrapling dependencies:$MISSING_DEPS"
    pip install $MISSING_DEPS 2>&1 | tail -3
  fi

  # Install browser binaries for stealth/dynamic modes
  if ! python3 -c "
import patchright
from pathlib import Path
import os
cache = Path.home() / 'Library' / 'Caches' / 'ms-playwright'
if not cache.exists():
    cache = Path.home() / '.cache' / 'ms-playwright'
has_chromium = any('chromium' in str(p) for p in cache.iterdir()) if cache.exists() else False
exit(0 if has_chromium else 1)
" 2>/dev/null; then
    log_step "Installing browser for Scrapling stealth mode..."
    python3 -m patchright install chromium 2>&1 | tail -3
  fi

  log_info "Scrapling ready (basic + stealth + dynamic modes)"
}

# ─── Launch Mode Selection ───────────────────────────────────────

select_launch_mode() {
  echo ""
  log_step "How do you want to run StackOwl?"
  echo ""
  echo -e "  ${BOLD}1)${RESET} 💻 CLI only"

  if has_telegram; then
    echo -e "  ${BOLD}2)${RESET} 📱 Telegram only"
    echo -e "  ${BOLD}3)${RESET} 💻+📱 CLI + Telegram"
    echo -e "  ${BOLD}4)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}5)${RESET} 🚀 All (CLI + Telegram + Web)"
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-5]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="telegram"; break ;;
        3) LAUNCH_MODE="telegram --with-cli"; break ;;
        4) LAUNCH_MODE="web"; break ;;
        5) LAUNCH_MODE="all"; break ;;
        *) log_error "Invalid choice. Enter 1-5." ;;
      esac
    done
  else
    echo -e "  ${BOLD}2)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}3)${RESET} 🚀 All (CLI + Web)"
    echo ""
    log_dim "Telegram not configured in stackowl.config.json — add telegram.botToken to enable it."
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-3]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="web"; break ;;
        3) LAUNCH_MODE="all"; break ;;
        *) log_error "Invalid choice. Enter 1-3." ;;
      esac
    done
  fi
}

# ─── Main ────────────────────────────────────────────────────────

main() {
  print_banner
  check_prerequisites

  # Show what config will be used (read-only, never modified)
  PROVIDER=$(json_read "$CONFIG_FILE" "defaultProvider")
  MODEL=$(json_read "$CONFIG_FILE" "defaultModel")
  log_info "Config: ${BOLD}$CONFIG_FILE${RESET}"
  log_dim  "Provider: ${PROVIDER:-unknown}  |  Model: ${MODEL:-unknown}"

  if [ -f "$SESSION_FILE" ]; then
    # Resume saved launch mode
    LAUNCH_MODE=$(json_read "$SESSION_FILE" "launchMode")
    SAVED_AT=$(json_read "$SESSION_FILE" "savedAt")
    echo ""
    log_info "Resuming saved launch mode: ${BOLD}$LAUNCH_MODE${RESET}"
    log_dim  "Saved: $SAVED_AT"
    log_dim  "Delete session.tmp to change the launch mode."
  else
    # First run — ask only about launch mode
    echo ""
    log_step "First run — choose how to start (this will be remembered)."
    select_launch_mode
    save_session
  fi

  echo ""
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  log_info "Starting StackOwl | mode: ${BOLD}$LAUNCH_MODE${RESET}"
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  echo ""

  write_pid_to_session "$$"

  cd "$SCRIPT_DIR"
  exec npx tsx src/index.ts $LAUNCH_MODE
}

main "$@"
