#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🦉 StackOwl — Start Script
# Interactive onboarding: provider selection + token management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/stackowl.config.json"
CREDENTIALS_FILE="$SCRIPT_DIR/.stackowl.credentials.json"

# ─── Colors ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

# ─── Banner ─────────────────────────────────────────────────────

print_banner() {
  echo ""
  echo -e "${YELLOW}   _____ __             __   ____            __${RESET}"
  echo -e "${YELLOW}  / ___// /_____ ______/ /__/ __ \\__      __/ /${RESET}"
  echo -e "${YELLOW}  \\__ \\/ __/ __ \`/ ___/ //_/ / / / | /| / / / ${RESET}"
  echo -e "${YELLOW} ___/ / /_/ /_/ / /__/ ,< / /_/ /| |/ |/ / /  ${RESET}"
  echo -e "${YELLOW}/____/\\__/\\__,_/\\___/_/|_|\\____/ |__/|__/_/   ${RESET}"
  echo -e "${DIM}──────────────────────────────────────────────────${RESET}"
  echo -e "${DIM}🦉 Personal AI Assistant • Challenge Everything${RESET}"
  echo -e "${DIM}──────────────────────────────────────────────────${RESET}"
  echo ""
}

# ─── Helpers ────────────────────────────────────────────────────

log_info()    { echo -e "${GREEN}✓${RESET} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${RESET} $1"; }
log_error()   { echo -e "${RED}✗${RESET} $1"; }
log_step()    { echo -e "${CYAN}▸${RESET} ${BOLD}$1${RESET}"; }
log_dim()     { echo -e "${DIM}  $1${RESET}"; }

# ─── Check Prerequisites ───────────────────────────────────────

check_prerequisites() {
  log_step "Checking prerequisites..."

  if ! command -v node &> /dev/null; then
    log_error "Node.js is not installed. Please install Node.js >= 22."
    exit 1
  fi

  NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_VERSION" -lt 22 ]; then
    log_error "Node.js >= 22 required (found v$(node -v))"
    exit 1
  fi
  log_info "Node.js $(node -v)"

  if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
    log_warn "Dependencies not installed. Running npm install..."
    (cd "$SCRIPT_DIR" && npm install)
  fi
  log_info "Dependencies installed"
}

# ─── Load existing credentials ──────────────────────────────────

load_credentials() {
  if [ -f "$CREDENTIALS_FILE" ]; then
    SAVED_PROVIDER=$(node -e "try{const c=JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE','utf8'));console.log(c.provider||'')}catch{console.log('')}" 2>/dev/null)
    SAVED_BASE_URL=$(node -e "try{const c=JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE','utf8'));console.log(c.baseUrl||'')}catch{console.log('')}" 2>/dev/null)
    SAVED_API_KEY=$(node -e "try{const c=JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE','utf8'));console.log(c.apiKey||'')}catch{console.log('')}" 2>/dev/null)
    SAVED_MODEL=$(node -e "try{const c=JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE','utf8'));console.log(c.model||'')}catch{console.log('')}" 2>/dev/null)
    SAVED_TELEGRAM_TOKEN=$(node -e "try{const c=JSON.parse(require('fs').readFileSync('$CREDENTIALS_FILE','utf8'));console.log(c.telegramBotToken||'')}catch{console.log('')}" 2>/dev/null)
  else
    SAVED_PROVIDER=""
    SAVED_BASE_URL=""
    SAVED_API_KEY=""
    SAVED_MODEL=""
    SAVED_TELEGRAM_TOKEN=""
  fi
}

# ─── Save credentials ──────────────────────────────────────────

save_credentials() {
  local provider="$1"
  local base_url="$2"
  local api_key="$3"
  local model="$4"

  cat > "$CREDENTIALS_FILE" << EOF
{
  "provider": "$provider",
  "baseUrl": "$base_url",
  "apiKey": "$api_key",
  "model": "$model",
  "telegramBotToken": "$TELEGRAM_TOKEN",
  "lastUpdated": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF

  chmod 600 "$CREDENTIALS_FILE"
  log_info "Credentials saved to .stackowl.credentials.json"
}

# ─── Update stackowl.config.json ───────────────────────────────

update_config() {
  local provider="$1"
  local base_url="$2"
  local api_key="$3"
  local model="$4"

  # Build provider-specific config
  local provider_json=""
  case "$provider" in
    ollama)
      provider_json="\"ollama\": { \"baseUrl\": \"$base_url\", \"defaultModel\": \"$model\" }"
      ;;
    openai)
      provider_json="\"openai\": { \"apiKey\": \"$api_key\", \"defaultModel\": \"$model\" }"
      ;;
    anthropic)
      provider_json="\"anthropic\": { \"apiKey\": \"$api_key\", \"defaultModel\": \"$model\" }"
      ;;
    openai-compatible)
      provider_json="\"openai\": { \"baseUrl\": \"$base_url\", \"apiKey\": \"$api_key\", \"defaultModel\": \"$model\" }"
      provider="openai"
      ;;
  esac

  local telegram_json=""
  if [ -n "$TELEGRAM_TOKEN" ]; then
    telegram_json=',
  "telegram": {
    "botToken": "'"$TELEGRAM_TOKEN"'",
    "enabled": true
  }'
  fi

  cat > "$CONFIG_FILE" << EOF
{
  "providers": {
    $provider_json
  },
  "defaultProvider": "$provider",
  "defaultModel": "$model",
  "workspace": "./workspace",
  "gateway": {
    "port": 3077,
    "host": "127.0.0.1"
  },
  "parliament": {
    "maxRounds": 3,
    "maxOwls": 6
  },
  "heartbeat": {
    "enabled": false,
    "intervalMinutes": 30
  },
  "owlDna": {
    "enabled": true,
    "evolutionBatchSize": 5,
    "decayRatePerWeek": 0.01
  }$telegram_json
}
EOF

  log_info "Config updated: stackowl.config.json"
}

# ─── Provider Selection ────────────────────────────────────────

select_provider() {
  echo ""
  log_step "Select your AI provider:"
  echo ""
  echo -e "  ${BOLD}1)${RESET} 🦙 Ollama          ${DIM}— Local or remote Ollama instance${RESET}"
  echo -e "  ${BOLD}2)${RESET} 🤖 OpenAI          ${DIM}— GPT-4o, GPT-4, etc.${RESET}"
  echo -e "  ${BOLD}3)${RESET} 🧠 Anthropic       ${DIM}— Claude Sonnet, Opus, etc.${RESET}"
  echo -e "  ${BOLD}4)${RESET} 🔌 OpenAI-Compatible ${DIM}— LM Studio, vLLM, etc.${RESET}"
  echo ""

  while true; do
    read -rp "$(echo -e "${CYAN}Enter choice [1-4]:${RESET} ")" choice
    case "$choice" in
      1) PROVIDER="ollama"; break ;;
      2) PROVIDER="openai"; break ;;
      3) PROVIDER="anthropic"; break ;;
      4) PROVIDER="openai-compatible"; break ;;
      *) log_error "Invalid choice. Please enter 1-4." ;;
    esac
  done
}

# ─── Collect Provider Details ──────────────────────────────────

collect_ollama_details() {
  echo ""
  log_step "Ollama Configuration"
  echo ""

  local default_url="${SAVED_BASE_URL:-http://127.0.0.1:11434}"
  read -rp "$(echo -e "${CYAN}Ollama server URL${RESET} ${DIM}[$default_url]:${RESET} ")" BASE_URL
  BASE_URL="${BASE_URL:-$default_url}"

  local default_model="${SAVED_MODEL:-llama3.2}"
  read -rp "$(echo -e "${CYAN}Model name${RESET} ${DIM}[$default_model]:${RESET} ")" MODEL
  MODEL="${MODEL:-$default_model}"

  API_KEY=""

  # Test connection
  echo ""
  log_step "Testing connection to $BASE_URL..."
  if curl -s --connect-timeout 5 "$BASE_URL/api/tags" > /dev/null 2>&1; then
    log_info "Connected to Ollama at $BASE_URL"

    # List available models
    MODELS=$(curl -s "$BASE_URL/api/tags" 2>/dev/null | node -e "
      let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{
        try{const j=JSON.parse(d);(j.models||[]).slice(0,8).forEach(m=>console.log('    • '+m.name))}
        catch{console.log('    (could not parse model list)')}
      })
    " 2>/dev/null)

    if [ -n "$MODELS" ]; then
      echo -e "${DIM}  Available models:${RESET}"
      echo "$MODELS"
    fi
  else
    log_warn "Cannot reach Ollama at $BASE_URL"
    log_dim "Make sure Ollama is running. You can still proceed — it will connect when available."
  fi
}

collect_openai_details() {
  echo ""
  log_step "OpenAI Configuration"
  echo ""

  if [ -n "$SAVED_API_KEY" ] && [ "$SAVED_PROVIDER" = "openai" ]; then
    local masked_key="${SAVED_API_KEY:0:7}...${SAVED_API_KEY: -4}"
    read -rp "$(echo -e "${CYAN}API Key${RESET} ${DIM}[saved: $masked_key]:${RESET} ")" API_KEY
    API_KEY="${API_KEY:-$SAVED_API_KEY}"
  else
    read -rp "$(echo -e "${CYAN}API Key (sk-...):${RESET} ")" API_KEY
    if [ -z "$API_KEY" ]; then
      log_error "API key is required for OpenAI."
      exit 1
    fi
  fi

  local default_model="${SAVED_MODEL:-gpt-4o}"
  read -rp "$(echo -e "${CYAN}Model${RESET} ${DIM}[$default_model]:${RESET} ")" MODEL
  MODEL="${MODEL:-$default_model}"

  BASE_URL="https://api.openai.com/v1"
}

collect_anthropic_details() {
  echo ""
  log_step "Anthropic Configuration"
  echo ""

  if [ -n "$SAVED_API_KEY" ] && [ "$SAVED_PROVIDER" = "anthropic" ]; then
    local masked_key="${SAVED_API_KEY:0:10}...${SAVED_API_KEY: -4}"
    read -rp "$(echo -e "${CYAN}API Key${RESET} ${DIM}[saved: $masked_key]:${RESET} ")" API_KEY
    API_KEY="${API_KEY:-$SAVED_API_KEY}"
  else
    read -rp "$(echo -e "${CYAN}API Key (sk-ant-...):${RESET} ")" API_KEY
    if [ -z "$API_KEY" ]; then
      log_error "API key is required for Anthropic."
      exit 1
    fi
  fi

  local default_model="${SAVED_MODEL:-claude-sonnet-4-20250514}"
  read -rp "$(echo -e "${CYAN}Model${RESET} ${DIM}[$default_model]:${RESET} ")" MODEL
  MODEL="${MODEL:-$default_model}"

  BASE_URL="https://api.anthropic.com"
}

collect_openai_compatible_details() {
  echo ""
  log_step "OpenAI-Compatible API Configuration"
  log_dim "Works with LM Studio, vLLM, text-generation-webui, etc."
  echo ""

  local default_url="${SAVED_BASE_URL:-http://127.0.0.1:1234/v1}"
  read -rp "$(echo -e "${CYAN}API Base URL${RESET} ${DIM}[$default_url]:${RESET} ")" BASE_URL
  BASE_URL="${BASE_URL:-$default_url}"

  if [ -n "$SAVED_API_KEY" ] && [ "$SAVED_PROVIDER" = "openai-compatible" ]; then
    local masked_key="${SAVED_API_KEY:0:7}...${SAVED_API_KEY: -4}"
    read -rp "$(echo -e "${CYAN}API Key${RESET} ${DIM}[saved: $masked_key, or 'none']:${RESET} ")" API_KEY
    API_KEY="${API_KEY:-$SAVED_API_KEY}"
  else
    read -rp "$(echo -e "${CYAN}API Key${RESET} ${DIM}[leave empty if not needed]:${RESET} ")" API_KEY
  fi

  local default_model="${SAVED_MODEL:-default}"
  read -rp "$(echo -e "${CYAN}Model name${RESET} ${DIM}[$default_model]:${RESET} ")" MODEL
  MODEL="${MODEL:-$default_model}"
}

# ─── Onboarding Flow ──────────────────────────────────────────

run_onboarding() {
  load_credentials

  # If we have saved credentials, offer to reuse them
  if [ -n "$SAVED_PROVIDER" ]; then
    echo ""
    log_info "Found saved configuration:"
    log_dim "Provider: $SAVED_PROVIDER"
    log_dim "Model: $SAVED_MODEL"
    if [ -n "$SAVED_BASE_URL" ]; then
      log_dim "URL: $SAVED_BASE_URL"
    fi
    if [ -n "$SAVED_API_KEY" ]; then
      local masked="${SAVED_API_KEY:0:7}...${SAVED_API_KEY: -4}"
      log_dim "API Key: $masked"
    fi
    echo ""

    read -rp "$(echo -e "${CYAN}Use saved configuration? [Y/n]:${RESET} ")" use_saved
    if [[ "$use_saved" =~ ^[Nn] ]]; then
      select_provider
    else
      PROVIDER="$SAVED_PROVIDER"
      BASE_URL="$SAVED_BASE_URL"
      API_KEY="$SAVED_API_KEY"
      MODEL="$SAVED_MODEL"
      TELEGRAM_TOKEN="${SAVED_TELEGRAM_TOKEN:-}"

      update_config "$PROVIDER" "$BASE_URL" "$API_KEY" "$MODEL"
      return
    fi
  else
    select_provider
  fi

  # Collect provider-specific details
  case "$PROVIDER" in
    ollama)              collect_ollama_details ;;
    openai)              collect_openai_details ;;
    anthropic)           collect_anthropic_details ;;
    openai-compatible)   collect_openai_compatible_details ;;
  esac

  # Ask about Telegram bot
  collect_telegram_details

  # Save credentials and update config
  save_credentials "$PROVIDER" "$BASE_URL" "$API_KEY" "$MODEL"
  update_config "$PROVIDER" "$BASE_URL" "$API_KEY" "$MODEL"
}

# ─── Telegram Bot Token ───────────────────────────────────────

collect_telegram_details() {
  echo ""
  log_step "Telegram Bot (optional)"
  log_dim "Talk to @BotFather on Telegram to create a bot and get a token."
  echo ""

  if [ -n "$SAVED_TELEGRAM_TOKEN" ]; then
    local masked="${SAVED_TELEGRAM_TOKEN:0:10}...${SAVED_TELEGRAM_TOKEN: -4}"
    read -rp "$(echo -e "${CYAN}Telegram Bot Token${RESET} ${DIM}[saved: $masked, or 'skip']:${RESET} ")" input
    if [ "$input" = "skip" ] || [ "$input" = "" ]; then
      TELEGRAM_TOKEN="$SAVED_TELEGRAM_TOKEN"
    else
      TELEGRAM_TOKEN="$input"
    fi
  else
    read -rp "$(echo -e "${CYAN}Telegram Bot Token${RESET} ${DIM}[leave empty to skip]:${RESET} ")" TELEGRAM_TOKEN
  fi

  if [ -n "$TELEGRAM_TOKEN" ]; then
    log_info "Telegram bot configured"
  else
    log_dim "Skipped Telegram. You can add it later in stackowl.config.json"
  fi
}

# ─── Channel Selection ────────────────────────────────────────

select_channel() {
  echo ""
  log_step "How do you want to chat?"
  echo ""
  echo -e "  ${BOLD}1)${RESET} 💻 CLI            ${DIM}— Chat in this terminal${RESET}"

  if [ -n "$TELEGRAM_TOKEN" ]; then
    echo -e "  ${BOLD}2)${RESET} 📱 Telegram       ${DIM}— Chat via Telegram bot${RESET}"
    echo -e "  ${BOLD}3)${RESET} 🔄 Both           ${DIM}— CLI + Telegram simultaneously${RESET}"
  fi
  echo ""

  while true; do
    if [ -n "$TELEGRAM_TOKEN" ]; then
      read -rp "$(echo -e "${CYAN}Enter choice [1-3]:${RESET} ")" channel_choice
      case "$channel_choice" in
        1) LAUNCH_MODE="chat"; break ;;
        2) LAUNCH_MODE="telegram"; break ;;
        3) LAUNCH_MODE="telegram --with-cli"; break ;;
        *) log_error "Invalid choice." ;;
      esac
    else
      LAUNCH_MODE="chat"
      break
    fi
  done
}

# ─── Main ──────────────────────────────────────────────────────

main() {
  print_banner
  check_prerequisites
  run_onboarding
  select_channel

  echo ""
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  log_info "Starting StackOwl..."
  echo -e "${DIM}─────────────────────────────────────────────────${RESET}"
  echo ""

  # Launch StackOwl
  cd "$SCRIPT_DIR"
  exec npx tsx src/index.ts $LAUNCH_MODE
}

main "$@"
