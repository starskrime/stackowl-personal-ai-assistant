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
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

log_info()    { echo -e "${GREEN}✓${RESET} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${RESET} $1"; }
log_error()   { echo -e "${RED}✗${RESET} $1"; }
log_step()    { echo -e "${CYAN}▸${RESET} ${BOLD}$1${RESET}"; }
log_dim()     { echo -e "${DIM}  $1${RESET}"; }
log_sub()     { echo -e "  ${DIM}↳${RESET} $1"; }
log_section() { echo -e "\n${BLUE}┌─${RESET} ${BOLD}$1${RESET}"; }
log_cmd()     { echo -e "  ${DIM}\$${RESET} $1"; }

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
  # Returns 0 (true) if stackowl.config.json has telegram.botToken set
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      process.exit(c.telegram && c.telegram.botToken && !c.telegram.botToken.includes('YOUR') ? 0 : 1);
    } catch { process.exit(1); }
  " 2>/dev/null
}

read_log_level() {
  # Reads logging.level from stackowl.config.json; returns "debug" if unset
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      console.log((c.logging && c.logging.level) || 'debug');
    } catch { console.log('debug'); }
  " 2>/dev/null
}

ensure_log_level() {
  # Writes logging.level = debug into config if not already set
  node -e "
    const fs = require('fs');
    let c = {};
    try { c = JSON.parse(fs.readFileSync('$CONFIG_FILE', 'utf8')); } catch { return; }
    if (!c.logging || !c.logging.level) {
      c.logging = Object.assign({}, c.logging || {}, { level: 'debug' });
      fs.writeFileSync('$CONFIG_FILE', JSON.stringify(c, null, 2));
    }
  " 2>/dev/null
}

has_slack() {
  # Returns 0 (true) if stackowl.config.json has slack.botToken and slack.appToken set
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      const ok = c.slack && c.slack.botToken && c.slack.appToken
        && !c.slack.botToken.includes('YOUR') && !c.slack.appToken.includes('YOUR');
      process.exit(ok ? 0 : 1);
    } catch { process.exit(1); }
  " 2>/dev/null
}

camofox_enabled() {
  # Returns 0 (true) if camofox.enabled === true in config
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      process.exit(c.camofox && c.camofox.enabled === true ? 0 : 1);
    } catch { process.exit(1); }
  " 2>/dev/null
}

camofox_url() {
  # Prints the configured CamoFox base URL (default: http://localhost:9377)
  node -e "
    try {
      const c = JSON.parse(require('fs').readFileSync('$CONFIG_FILE', 'utf8'));
      console.log((c.camofox && c.camofox.baseUrl) || 'http://localhost:9377');
    } catch { console.log('http://localhost:9377'); }
  " 2>/dev/null
}

write_camofox_config() {
  # Writes camofox config block into stackowl.config.json
  local base_url="$1"
  node -e "
    const fs = require('fs');
    let c = {};
    try { c = JSON.parse(fs.readFileSync('$CONFIG_FILE', 'utf8')); } catch {}
    c.camofox = { enabled: true, baseUrl: '$base_url', apiKey: null, defaultUserId: 'stackowl', defaultTimeout: 30000 };
    fs.writeFileSync('$CONFIG_FILE', JSON.stringify(c, null, 2));
  "
}

camofox_server_running() {
  # Returns 0 (true) if CamoFox server responds at the given URL
  local url="$1"
  curl -sf --connect-timeout 2 "${url}/tabs" -o /dev/null 2>/dev/null
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

# ─── Code Sync ──────────────────────────────────────────────────

sync_code() {
  log_section "Syncing latest code"
  log_sub "Running: git pull"
  local pull_out
  if pull_out=$(git -C "$SCRIPT_DIR" pull 2>&1); then
    if echo "$pull_out" | grep -q "Already up to date"; then
      log_info "Already up to date"
    else
      echo "$pull_out" | while IFS= read -r line; do log_dim "$line"; done
      log_info "Code updated"
    fi
  else
    log_warn "git pull failed — continuing with local version"
    echo "$pull_out" | while IFS= read -r line; do log_dim "$line"; done
  fi
}

# ─── Prerequisites ───────────────────────────────────────────────

load_nvm() {
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  # shellcheck source=/dev/null
  # Use || true so set -e doesn't exit when nvm is not yet installed
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh" || true
  [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion" || true
}

install_node_via_nvm() {
  log_sub "Method: nvm (Node Version Manager)"
  if [ ! -s "$HOME/.nvm/nvm.sh" ]; then
    log_sub "nvm not found — installing nvm v0.39.7..."
    if command -v curl &>/dev/null; then
      log_cmd "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
      curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    elif command -v wget &>/dev/null; then
      log_cmd "wget -qO- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash"
      wget -qO- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    else
      log_error "curl and wget both missing — cannot install nvm."
      log_error "Install Node.js 22 manually: https://nodejs.org"
      exit 1
    fi
    log_info "nvm installed"
  else
    log_sub "nvm already present — using it"
  fi
  load_nvm
  log_sub "Installing Node.js 22..."
  log_cmd "nvm install 22"
  nvm install 22
  log_cmd "nvm use 22 && nvm alias default 22"
  nvm use 22
  nvm alias default 22
}

install_node_via_nodesource_apt() {
  log_sub "Method: NodeSource apt repository"
  log_sub "Step 1/2 — downloading NodeSource setup script..."
  if command -v curl &>/dev/null; then
    log_cmd "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -"
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  elif command -v wget &>/dev/null; then
    log_cmd "wget -qO- https://deb.nodesource.com/setup_22.x | sudo -E bash -"
    wget -qO- https://deb.nodesource.com/setup_22.x | sudo -E bash -
  else
    log_warn "curl/wget not found — falling back to nvm"
    install_node_via_nvm
    return
  fi
  log_sub "Step 2/2 — installing nodejs package..."
  log_cmd "sudo apt-get install -y nodejs"
  sudo apt-get install -y nodejs
}

install_node_via_nodesource_rpm() {
  local mgr="$1"
  log_sub "Method: NodeSource rpm repository (${mgr})"
  log_sub "Step 1/2 — downloading NodeSource setup script..."
  if command -v curl &>/dev/null; then
    log_cmd "curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -"
    curl -fsSL https://rpm.nodesource.com/setup_22.x | sudo bash -
  else
    log_warn "curl not found — falling back to nvm"
    install_node_via_nvm
    return
  fi
  log_sub "Step 2/2 — installing nodejs package..."
  log_cmd "sudo ${mgr} install -y nodejs"
  sudo "$mgr" install -y nodejs
}

install_node_auto() {
  local OS
  OS="$(uname -s)"
  log_warn "Node.js >= 22 not found — starting automatic installation"
  log_sub "Detected OS: ${OS}"
  echo ""

  case "$OS" in
    Darwin)
      if command -v brew &>/dev/null; then
        log_sub "Method: Homebrew"
        log_cmd "brew install node@22"
        brew install node@22
        log_sub "Linking node@22..."
        brew link --overwrite node@22 2>/dev/null || true
        export PATH="/opt/homebrew/opt/node@22/bin:/usr/local/opt/node@22/bin:$PATH"
      else
        log_warn "Homebrew not found — falling back to nvm"
        install_node_via_nvm
      fi
      ;;
    Linux)
      if command -v apt-get &>/dev/null; then
        log_sub "Detected package manager: apt (Debian/Ubuntu)"
        install_node_via_nodesource_apt
      elif command -v dnf &>/dev/null; then
        log_sub "Detected package manager: dnf (Fedora/RHEL)"
        install_node_via_nodesource_rpm dnf
      elif command -v yum &>/dev/null; then
        log_sub "Detected package manager: yum (CentOS/RHEL)"
        install_node_via_nodesource_rpm yum
      elif command -v pacman &>/dev/null; then
        log_sub "Method: pacman (Arch Linux)"
        log_cmd "sudo pacman -Sy --noconfirm nodejs npm"
        sudo pacman -Sy --noconfirm nodejs npm
      elif command -v apk &>/dev/null; then
        log_sub "Method: apk (Alpine Linux)"
        log_cmd "sudo apk add --no-cache nodejs npm"
        sudo apk add --no-cache nodejs npm
      else
        log_warn "No known package manager found — falling back to nvm"
        install_node_via_nvm
      fi
      ;;
    *)
      log_error "Unsupported OS: $OS"
      log_error "Please install Node.js 22 manually: https://nodejs.org"
      exit 1
      ;;
  esac

  # Re-source nvm in case it was just installed
  load_nvm

  if ! command -v node &>/dev/null; then
    log_error "Automatic installation did not succeed."
    log_error "Please install Node.js 22 manually: https://nodejs.org"
    exit 1
  fi
  log_info "Node.js $(node -v) installed successfully"
}

check_prerequisites() {
  # Source nvm early so a previously-installed nvm Node is visible
  load_nvm

  echo ""
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  log_step "Checking prerequisites..."

  # ── [1/3] Node.js ──────────────────────────────────────────────
  log_section "[1/3] Node.js"
  if ! command -v node &>/dev/null; then
    install_node_auto
  fi

  NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_VERSION" -lt 22 ]; then
    log_warn "Node.js >= 22 required (found $(node -v)) — upgrading automatically..."
    install_node_auto
    NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
    if [ "$NODE_VERSION" -lt 22 ]; then
      log_error "Could not upgrade to Node.js 22. Please upgrade manually."
      exit 1
    fi
  fi
  log_info "Node.js $(node -v)"

  # ── [2/3] npm dependencies ─────────────────────────────────────
  log_section "[2/3] npm dependencies"
  if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
    log_sub "First run — installing packages. This takes 2–3 minutes on a fresh machine."
    log_sub "You will see npm output below:"
    echo ""
    log_cmd "npm install (in $SCRIPT_DIR)"
    echo ""
    (cd "$SCRIPT_DIR" && sudo npm install --unsafe-perm)
    echo ""
    log_info "npm dependencies installed"
  else
    log_info "npm dependencies already installed"
    log_sub "Delete node_modules/ and re-run to force a reinstall"
  fi

  # ── [3/3] Python & Scrapling ────────────────────────────────────
  log_section "[3/3] Python & Scrapling (anti-bot web scraping)"
  local SCRAPLING_MARKER="$SCRIPT_DIR/.scrapling_ready"
  if [ ! -f "$SCRAPLING_MARKER" ]; then
    install_scrapling && touch "$SCRAPLING_MARKER"
  else
    log_info "Scrapling ready"
    log_sub "Delete .scrapling_ready to force a reinstall"
  fi

  echo ""
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  log_info "All prerequisites satisfied"
  # CamoFox install moved to: stackowl backends install camofox
}

install_scrapling() {
  if ! command -v python3 &>/dev/null; then
    log_warn "Python 3 not found — scrapling_fetch tool will be unavailable."
    log_sub "Install Python 3.8+ to enable anti-bot web scraping."
    return
  fi
  log_sub "Python: $(python3 --version)"

  # Resolve pip: prefer pip3, fall back to pip, fall back to python3 -m pip
  local PIP
  if command -v pip3 &>/dev/null; then
    PIP="pip3"
  elif command -v pip &>/dev/null; then
    PIP="pip"
  else
    PIP="python3 -m pip"
  fi
  log_sub "pip: $PIP"

  # Install scrapling if missing
  if python3 -c "import scrapling" 2>/dev/null; then
    log_info "Scrapling already installed"
  else
    log_sub "Installing scrapling[all] — this downloads several ML packages, may take a minute..."
    echo ""
    log_cmd "sudo $PIP install scrapling[all]"
    echo ""
    sudo $PIP install "scrapling[all]"
    echo ""
    if python3 -c "import scrapling" 2>/dev/null; then
      log_info "scrapling installed"
    else
      log_warn "scrapling installation failed — scrapling_fetch tool will be unavailable."
      log_sub "Try manually: sudo $PIP install scrapling[all]"
      return
    fi
  fi

  # Check for and install any missing optional dependencies
  log_sub "Checking optional scrapling dependencies..."
  local MISSING_DEPS=""
  for pkg in curl_cffi browserforge playwright patchright msgspec; do
    if python3 -c "import $pkg" 2>/dev/null; then
      log_sub "  $pkg ✓"
    else
      log_sub "  $pkg — not found, will install"
      MISSING_DEPS="$MISSING_DEPS $pkg"
    fi
  done

  if [ -n "$MISSING_DEPS" ]; then
    echo ""
    log_sub "Installing missing packages:$MISSING_DEPS"
    log_cmd "sudo $PIP install$MISSING_DEPS"
    echo ""
    sudo $PIP install $MISSING_DEPS
    echo ""
    log_info "Scrapling dependencies installed"
  else
    log_info "All scrapling dependencies present"
  fi

  # Install browser binaries for stealth/dynamic modes
  if ! python3 -c "
import patchright
from pathlib import Path
cache = Path.home() / 'Library' / 'Caches' / 'ms-playwright'
if not cache.exists():
    cache = Path.home() / '.cache' / 'ms-playwright'
has_chromium = any('chromium' in str(p) for p in cache.iterdir()) if cache.exists() else False
exit(0 if has_chromium else 1)
" 2>/dev/null; then
    log_sub "Installing Chromium browser for stealth mode (downloads ~150 MB)..."
    echo ""
    log_cmd "sudo python3 -m patchright install chromium"
    echo ""
    sudo python3 -m patchright install chromium
    echo ""
    log_info "Chromium browser installed"
  else
    log_info "Chromium browser already installed"
  fi

  log_info "Scrapling ready (basic + stealth + dynamic modes)"
}

# ─── CamoFox Setup ──────────────────────────────────────────────
# CamoFox install moved to: stackowl backends install camofox

show_camofox_docker() {
  local url="$1"
  local port
  port=$(echo "$url" | sed 's/.*:\([0-9][0-9]*\).*/\1/'); [[ "$port" =~ ^[0-9]+$ ]] || port="9377"
  echo ""
  log_step "Run this Docker command (in a separate terminal):"
  echo ""
  echo -e "  ${BOLD}docker run -d --name camofox -p ${port}:9377 jojoinc/camofox:latest${RESET}"
  echo ""
  log_dim "Or with Docker Compose — see: https://github.com/jo-inc/camofox-browser"
  log_dim "CamoFox will be available at $url after the container starts."
}

# ─── Launch Mode Selection ───────────────────────────────────────

select_launch_mode() {
  echo ""
  log_step "How do you want to run StackOwl?"
  echo ""

  local HAS_TG=false
  local HAS_SL=false
  has_telegram && HAS_TG=true
  has_slack && HAS_SL=true

  echo -e "  ${BOLD}1)${RESET} 💻 CLI only"

  if $HAS_TG && $HAS_SL; then
    echo -e "  ${BOLD}2)${RESET} 📱 Telegram only"
    echo -e "  ${BOLD}3)${RESET} 💬 Slack only"
    echo -e "  ${BOLD}4)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}5)${RESET} 🚀 All (CLI + Telegram + Slack + Web)"
    echo -e "  ${BOLD}6)${RESET} 🎤 Voice (offline mic → Whisper STT → owl → macOS say)"
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-6]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="telegram"; break ;;
        3) LAUNCH_MODE="slack"; break ;;
        4) LAUNCH_MODE="web"; break ;;
        5) LAUNCH_MODE="all"; break ;;
        6) LAUNCH_MODE="voice --voice Samantha --model small.en"; break ;;
        *) log_error "Invalid choice. Enter 1-6." ;;
      esac
    done
  elif $HAS_TG; then
    echo -e "  ${BOLD}2)${RESET} 📱 Telegram only"
    echo -e "  ${BOLD}3)${RESET} 💻+📱 CLI + Telegram"
    echo -e "  ${BOLD}4)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}5)${RESET} 🚀 All (CLI + Telegram + Web)"
    echo -e "  ${BOLD}6)${RESET} 🎤 Voice (offline mic → Whisper STT → owl → macOS say)"
    echo ""
    log_dim "Slack not configured — add slack.botToken + slack.appToken to enable it."
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-6]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="telegram"; break ;;
        3) LAUNCH_MODE="telegram --with-cli"; break ;;
        4) LAUNCH_MODE="web"; break ;;
        5) LAUNCH_MODE="all"; break ;;
        6) LAUNCH_MODE="voice --voice Samantha --model small.en"; break ;;
        *) log_error "Invalid choice. Enter 1-6." ;;
      esac
    done
  elif $HAS_SL; then
    echo -e "  ${BOLD}2)${RESET} 💬 Slack only"
    echo -e "  ${BOLD}3)${RESET} 💻+💬 CLI + Slack"
    echo -e "  ${BOLD}4)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}5)${RESET} 🚀 All (CLI + Slack + Web)"
    echo -e "  ${BOLD}6)${RESET} 🎤 Voice (offline mic → Whisper STT → owl → macOS say)"
    echo ""
    log_dim "Telegram not configured — add telegram.botToken to enable it."
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-6]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="slack"; break ;;
        3) LAUNCH_MODE="slack --with-cli"; break ;;
        4) LAUNCH_MODE="web"; break ;;
        5) LAUNCH_MODE="all"; break ;;
        6) LAUNCH_MODE="voice --voice Samantha --model small.en"; break ;;
        *) log_error "Invalid choice. Enter 1-6." ;;
      esac
    done
  else
    echo -e "  ${BOLD}2)${RESET} 🌐 Web UI"
    echo -e "  ${BOLD}3)${RESET} 🚀 All (CLI + Web)"
    echo -e "  ${BOLD}4)${RESET} 🎤 Voice (offline mic → Whisper STT → owl → macOS say)"
    echo ""
    log_dim "Telegram/Slack not configured — add tokens to stackowl.config.json to enable them."
    echo ""
    while true; do
      read -rp "$(echo -e "${CYAN}Enter choice [1-4]:${RESET} ")" ch
      case "$ch" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="web"; break ;;
        3) LAUNCH_MODE="all"; break ;;
        4) LAUNCH_MODE="voice --voice Samantha --model small.en"; break ;;
        *) log_error "Invalid choice. Enter 1-4." ;;
      esac
    done
  fi
}

# ─── Main ────────────────────────────────────────────────────────

main() {
  print_banner
  sync_code
  check_prerequisites

  # Show what config will be used (read-only, never modified)
  PROVIDER=$(json_read "$CONFIG_FILE" "defaultProvider")
  MODEL=$(json_read "$CONFIG_FILE" "defaultModel")
  log_info "Config: ${BOLD}$CONFIG_FILE${RESET}"
  log_dim  "Provider: ${PROVIDER:-unknown}  |  Model: ${MODEL:-unknown}"

  # Ensure logging.level = debug is set in config (default for observability)
  ensure_log_level
  LOG_LEVEL=$(read_log_level)
  log_dim  "Log level: ${LOG_LEVEL}"

  if [ ! -f "$CONFIG_FILE" ]; then
    # No config yet — skip launch-mode selection; onboarding wizard runs inside tsx
    LAUNCH_MODE="chat"
  elif [ -f "$SESSION_FILE" ]; then
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

  # Free port 3000 if a stale process is holding it
  local stale_pid
  stale_pid=$(lsof -ti :3000 2>/dev/null || true)
  if [[ -n "$stale_pid" ]]; then
    log_info "Freeing port 3000 (stale pid $stale_pid)..."
    kill -9 "$stale_pid" 2>/dev/null || true
    sleep 0.5
  fi

  cd "$SCRIPT_DIR"
  exec npx tsx src/index.ts $LAUNCH_MODE
}

main "$@"
