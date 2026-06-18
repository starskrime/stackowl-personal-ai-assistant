#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# StackOwl — Full Reset
# Wipes everything the assistant created.
# Preserves: stackowl.config.json, src/, dist/, node_modules/
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="$SCRIPT_DIR/workspace"

RED='\033[0;31m'
YEL='\033[1;33m'
GRN='\033[0;32m'
BLD='\033[1m'
RST='\033[0m'

echo ""
echo -e "${BLD}🦉 StackOwl — Full Reset${RST}"
echo "────────────────────────────────────────"
echo ""
echo -e "${YEL}Everything the assistant created will be permanently deleted:${RST}"
echo ""
echo "  Knowledge     pellets, LanceDB, Kuzu, knowledge graph"
echo "  Memory        sessions, episodic memory, facts, digests, memory.md"
echo "  Personality   owl DNA, inner state, mutation history, SOUL.md"
echo "  Preferences   preferences.json, inferred profile, user name"
echo "  Learning      evolution, skills stats, owl learnings"
echo "  Runtime       SQLite DBs, cron jobs, intents, Oscar state"
echo "  Artifacts     screenshots, downloads, workflows, tools, recipes"
echo "  Other         quests, journal, constellations, capsules, logs"
echo ""
echo -e "${GRN}Preserved:${RST}"
echo "  stackowl.config.json  (API keys, provider settings)"
echo "  src/                  (source code)"
echo "  dist/                 (compiled code)"
echo "  node_modules/         (dependencies)"
echo ""

read -p "$(echo -e "${RED}${BLD}Type YES to confirm full reset: ${RST}")" CONFIRM
if [[ "$CONFIRM" != "YES" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo -e "${BLD}Wiping...${RST}"
echo ""

wipe_dir() {
  if [[ -d "$1" ]]; then
    rm -rf "$1"
    echo "  ✓ $1"
  fi
}

wipe_file() {
  if [[ -f "$1" ]]; then
    rm -f "$1"
    echo "  ✓ $1"
  fi
}

# ── Knowledge ────────────────────────────────────────────────────
wipe_dir  "$WS/pellets"
wipe_dir  "$WS/.pellets_lance"
wipe_dir  "$WS/.pellets_kuzu"
wipe_file "$WS/knowledge_graph.json"
wipe_dir  "$WS/constellations"

# ── Memory ───────────────────────────────────────────────────────
wipe_dir  "$WS/sessions"
wipe_dir  "$WS/memory"
wipe_file "$WS/memory.md"

# ── Personality ──────────────────────────────────────────────────
find "$WS/owls" -name "owl_dna.json"           -exec rm -f {} + 2>/dev/null || true
find "$WS/owls" -name "inner_state.json"       -exec rm -f {} + 2>/dev/null || true
find "$WS/owls" -name "mutation-tracker.json"  -exec rm -f {} + 2>/dev/null || true
echo "  ✓ $WS/owls (DNA, inner state, mutation history)"
wipe_file "$WS/SOUL.md"

# ── Preferences & user identity ──────────────────────────────────
wipe_file "$WS/preferences.json"
wipe_dir  "$WS/preferences"
wipe_file "$WS/user-profile.json"
wipe_file "$WS/user_name.txt"
wipe_file "$WS/known_chat_ids.json"

# ── Learning & evolution ─────────────────────────────────────────
wipe_dir  "$WS/evolution"
wipe_file "$WS/skills-stats.json"
wipe_file "$WS/noctua_improvement_prompt.txt"

# ── SQLite databases ─────────────────────────────────────────────
find "$WS" -maxdepth 4 -name "stackowl.db*" -exec rm -f {} + 2>/dev/null || true
echo "  ✓ SQLite databases"

# ── Runtime state ────────────────────────────────────────────────
wipe_file "$WS/cron-jobs.json"
wipe_dir  "$WS/intents"
wipe_dir  "$WS/oscar"
wipe_dir  "$WS/capsules"

# ── Skills (learned, not default) ────────────────────────────────
# Keep src/skills/defaults — those are code. Wipe workspace/skills (runtime-generated).
wipe_dir  "$WS/skills"

# ── Artifacts the assistant created ──────────────────────────────
wipe_dir  "$WS/screenshots"
wipe_dir  "$WS/downloads"
wipe_dir  "$WS/tools"
wipe_dir  "$WS/workflows"
wipe_dir  "$WS/recipes"
wipe_dir  "$WS/instagram_reel"
wipe_dir  "$WS/logs"
wipe_dir  "$WS/quests"
wipe_dir  "$WS/journal"

# ── Misc assistant-generated files ───────────────────────────────
wipe_file "$WS/notes.txt"
wipe_file "$WS/test-output.txt"
wipe_file "$WS/ai_news.xml"
wipe_file "$WS/ai_news_fetcher.sh"
wipe_file "$WS/scrape_news.js"
wipe_file "$WS/screenshot_tool.sh"
wipe_file "$WS/download_reel.py"

# ── Browser data ─────────────────────────────────────────────────
wipe_dir  "$WS/.browser-data"
wipe_dir  "$WS/.browser-profiles"

# ── Nested workspace ─────────────────────────────────────────────
wipe_dir  "$WS/workspace"
wipe_dir  "$WS/src"

echo ""
echo -e "${GRN}${BLD}✓ Reset complete.${RST}"
echo ""
echo "Noctua is now a new born. Start with: npm run dev"
echo ""
