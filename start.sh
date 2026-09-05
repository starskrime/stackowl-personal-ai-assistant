#!/usr/bin/env bash
# Always kill every previous StackOwl process (including orphaned __core__
# children that survive their parent gateway's shutdown — incident
# 2026-07-22: an orphaned `__core__` process reparented to init kept port
# 8766 bound after a normal `stop` + `start`, blocking the new instance)
# before starting one fresh instance. This is the canonical restart entry
# point for this repo — use it instead of calling `stop`/`start` by hand.
#
# D18.3, 2026-09-05: this script used to hardcode "$HOME/.stackowl" and to
# sweep by PROCESS NAME alone, so it was blind to which home it was acting on.
# With STACKOWL_HOME set it would have killed the OTHER instance, deleted its
# pid and socket, and overwritten its stdout log — while the platform's own 25
# path accessors correctly resolved into the new home. It now ASKS the single
# accessor for the home rather than reimplementing the path, and scopes the
# sweep to processes belonging to that home.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# One source of truth for the home. The shell asks StackowlHome rather than
# re-deriving ~/.stackowl, so STACKOWL_HOME and the legacy per-path overrides
# are honoured here exactly as they are in src/.
home="$(uv run python -c 'from stackowl.paths import StackowlHome; print(StackowlHome.home())' 2>/dev/null)"
if [ -z "$home" ]; then
    echo "[start.sh] FATAL: could not resolve the StackOwl home from StackowlHome." >&2
    echo "[start.sh] Refusing to sweep or delete anything without knowing which instance." >&2
    exit 1
fi
echo "[start.sh] home: $home"

# The STACKOWL_HOME a running process was launched with, or the default home
# when it carries none. Empty output means "could not determine".
process_home() {
    local pid="$1" raw=""
    if [ -r "/proc/$pid/environ" ]; then
        raw="$(tr '\0' '\n' < "/proc/$pid/environ" | grep -m1 '^STACKOWL_HOME=' || true)"
    elif command -v ps >/dev/null 2>&1; then
        raw="$(ps eww -p "$pid" 2>/dev/null | tr ' ' '\n' | grep -m1 '^STACKOWL_HOME=' || true)"
    else
        return 1
    fi
    if [ -n "$raw" ]; then
        printf '%s\n' "${raw#STACKOWL_HOME=}"
    else
        # No override in that process's environment: it runs on the default home,
        # which is what StackowlHome reports when STACKOWL_HOME is unset.
        STACKOWL_HOME= uv run python -c 'from stackowl.paths import StackowlHome; print(StackowlHome.home())' 2>/dev/null
    fi
}

# PIDs of StackOwl processes belonging to THIS home only. A name-only match
# would sweep every instance on the box.
instance_pids() {
    local pid ph
    for pid in $(pgrep -f "python3? -m stackowl (start|__core__)" || true); do
        ph="$(process_home "$pid")"
        if [ -z "$ph" ]; then
            echo "[start.sh] WARNING: cannot determine the home of pid $pid — leaving it alone." >&2
            continue
        fi
        [ "$ph" = "$home" ] && printf '%s\n' "$pid"
    done
}

echo "[start.sh] stopping any running instance..."
uv run python -m stackowl stop 2>/dev/null

# Graceful shutdown can take a few seconds; give it room before force-killing.
for _ in $(seq 1 10); do
    [ -z "$(instance_pids)" ] && break
    sleep 1
done

leftover="$(instance_pids | tr '\n' ' ')"
if [ -n "${leftover// /}" ]; then
    echo "[start.sh] force-killing leftover/orphaned processes: $leftover"
    # shellcheck disable=SC2086
    kill -TERM $leftover 2>/dev/null
    sleep 2
    leftover="$(instance_pids | tr '\n' ' ')"
    if [ -n "${leftover// /}" ]; then
        # shellcheck disable=SC2086
        kill -KILL $leftover 2>/dev/null
    fi
fi

runtime_dir="$home/runtime"
if [ -d "$runtime_dir" ] && [ -z "$(instance_pids)" ]; then
    echo "[start.sh] clearing stale runtime files"
    rm -f "$runtime_dir/stackowl.pid" "$runtime_dir/core.sock"
fi

echo "[start.sh] starting fresh instance..."
nohup uv run python -m stackowl start > "$home/manual_restart_stdout.log" 2>&1 &
disown

echo "[start.sh] launched (pid $!). Tail $home/logs/stackowl.jsonl to confirm steady state."
