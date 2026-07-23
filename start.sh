#!/usr/bin/env bash
# Always kill every previous StackOwl process (including orphaned __core__
# children that survive their parent gateway's shutdown — incident
# 2026-07-22: an orphaned `__core__` process reparented to init kept port
# 8766 bound after a normal `stop` + `start`, blocking the new instance)
# before starting one fresh instance. This is the canonical restart entry
# point for this repo — use it instead of calling `stop`/`start` by hand.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "[start.sh] stopping any running instance..."
uv run python -m stackowl stop 2>/dev/null

# Graceful shutdown can take a few seconds; give it room before force-killing.
for _ in $(seq 1 10); do
    pgrep -f "python3? -m stackowl (start|__core__)" >/dev/null 2>&1 || break
    sleep 1
done

leftover="$(pgrep -f "python3? -m stackowl (start|__core__)" || true)"
if [ -n "$leftover" ]; then
    echo "[start.sh] force-killing leftover/orphaned processes: $leftover"
    # shellcheck disable=SC2086
    kill -TERM $leftover 2>/dev/null
    sleep 2
    leftover="$(pgrep -f "python3? -m stackowl (start|__core__)" || true)"
    if [ -n "$leftover" ]; then
        # shellcheck disable=SC2086
        kill -KILL $leftover 2>/dev/null
    fi
fi

runtime_dir="$HOME/.stackowl/runtime"
if [ -d "$runtime_dir" ] && [ -z "$(pgrep -f "python3? -m stackowl (start|__core__)" || true)" ]; then
    echo "[start.sh] clearing stale runtime files"
    rm -f "$runtime_dir/stackowl.pid" "$runtime_dir/core.sock"
fi

echo "[start.sh] starting fresh instance..."
nohup uv run python -m stackowl start > "$HOME/.stackowl/manual_restart_stdout.log" 2>&1 &
disown

echo "[start.sh] launched (pid $!). Tail ~/.stackowl/logs/stackowl.jsonl to confirm steady state."
