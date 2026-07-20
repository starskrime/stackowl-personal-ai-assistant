# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mandatory skill

**`tonyStyle` (`.claude/skills/tonyStyle/SKILL.md`) is a main skill for this repo — invoke it on every task that touches StackOwl code**, not only when its description happens to match. It scans the wider codebase (not just the current diff) for real defects — silent catches, disabled/stubbed features, architecture violations, missing 4-point logging, dead code masking bugs — and fixes them in the same turn with minimal root-cause diffs, gated on tests+lint+type-check staying green. It composes with (doesn't replace) `systematic-debugging`, `verification-before-completion`, and `code-review`.

## Commands (Python — run from the repo root)

```bash
uv run python -m stackowl --help     # Show all CLI commands
uv run python -m stackowl setup --minimal  # Interactive 3-step onboarding
uv run python -m stackowl serve      # Start the StackOwl server
uv run python -m stackowl health     # Check system health
uv run python -m stackowl db migrate # Apply pending schema migrations
uv run python -m stackowl db backup --output <path>  # Hot backup
uv run pytest                        # Run all tests
uv run pytest tests/path/to/test.py  # Run a single test file
uv run ruff check src/               # Lint
uv run mypy src/                     # Type-check (strict)
```

First-time setup (from the repo root):
```bash
uv sync                              # Install dependencies
uv run python -m stackowl --version  # Smoke test
```

> Note: the Python project was migrated from the `v2/` subfolder to the repo root.
> The previous root (v1 TypeScript app + earlier infra) is archived under `old/`.

## Architecture Overview

StackOwl is a personal AI assistant framework built around **owl personas** with evolving personalities, multi-model routing, and structured knowledge management. It is a pure-Python package rooted at `src/stackowl/` (see `project_v2_to_root_migration` — the prior Node/TypeScript v1 app is archived under `old/` and is not part of the live tree).

### Core Concepts

- **Owls** — AI personas with DNA (personality traits, learned preferences, expertise growth) that mutate over time based on interactions (`src/stackowl/owls/`)
- **Parliament** — Multi-owl brainstorming: multi-round debate (positions → cross-examination → synthesis), outputs a knowledge pellet (`src/stackowl/parliament/`)
- **Instincts / Perches** — Reactive behavioral triggers and passive context observers that feed constraints back into the pipeline
- **Heartbeat / Scheduler** — Proactive notification and job scheduling; owls reach out to users on configurable schedules via Telegram/Slack/Discord/WhatsApp (`src/stackowl/scheduler/`, `src/stackowl/notifications/`)

### Key Module Responsibilities

| Package | Responsibility |
|--------|---------------|
| `src/stackowl/pipeline/` | Core turn pipeline: receive → think → tool calls → observe → respond |
| `src/stackowl/gateway/` | Durable gateway process (channel adapters, session dispatch, supervised restart) |
| `src/stackowl/interaction/` | Turn-level orchestration between the gateway and the pipeline |
| `src/stackowl/owls/` | Owl registry, DNA storage/mutation, routing, delegation |
| `src/stackowl/parliament/` | Multi-owl debate orchestration + pellet generation |
| `src/stackowl/scheduler/` | Job scheduler, handlers (morning_brief, check_in, health_sweep, evolution, etc.) |
| `src/stackowl/notifications/` | `NotificationRouter` (decision) + `ProactiveDeliverer` (transport) + delivery ledger |
| `src/stackowl/channels/` | Channel adapters — `telegram/`, `discord/`, `slack/`, `whatsapp/` — plus the `ChannelRegistry` |
| `src/stackowl/memory/` | Memory bridge, embeddings, recall/search |
| `src/stackowl/providers/` | AI backend implementations, model routing |
| `src/stackowl/tools/` | Tool registry and built-in tools available to the pipeline |
| `src/stackowl/config/` | `Settings` (pydantic-settings) — YAML file + `STACKOWL_*` env vars |
| `src/stackowl/infra/observability.py` | JSONL logging, `TraceContext`, named loggers |

### Data Flow

1. A channel adapter (Telegram/Slack/Discord/WhatsApp/CLI/TUI) receives user input and mints a trace via `TraceContext.start(...)`
2. The gateway hands the turn to `src/stackowl/interaction/` which drives the pipeline (`src/stackowl/pipeline/`)
3. The pipeline runs its receive → think → act (tool calls) → observe → respond loop until a response is ready
4. `src/stackowl/owls/evolution.py`-family jobs periodically mutate owl DNA based on interaction history (scheduled, not inline)
5. Parliament sessions run multiple owls in debate → the pellet generator captures structured output
6. Proactive/scheduled work (`src/stackowl/scheduler/`) is dispatched by the job scheduler, decided by `NotificationRouter`, and transported by `ProactiveDeliverer` through a registered channel adapter

### Configuration

Config lives in `stackowl.yaml` (path overridable via `STACKOWL_CONFIG_FILE`) and is loaded by the `Settings` class (`src/stackowl/config/settings.py`, `pydantic-settings`). Env vars (`STACKOWL_*`, `__`-nested) take priority over the YAML file. Key top-level sections include `providers`, `parliament`, `memory`, `scheduler`, `brief`, `check_in`, `telegram_channel`, `system`, `sandbox`. API keys/tokens live under their section (e.g. `telegram_channel.bot_token`) and are marked `sensitive` so logging redacts them.

### Tech Stack

- **Runtime:** Python ≥3.13
- **CLI:** `typer`
- **TUI:** `textual`
- **Channels:** `python-telegram-bot`, `discord.py`, `slack-bolt`
- **Config:** `pydantic` / `pydantic-settings`
- **Tests:** `pytest` (+ `pytest-asyncio`)
- **Lint/types:** `ruff`, `mypy` (strict)
- **Package/env manager:** `uv`

---

## Observability & Debugging

### Structured Logging

Every module has a named logger via `src/stackowl/infra/observability.py`:

```python
from stackowl.infra.observability import log

log.tool.debug("shell.execute: entry", extra={"_fields": {"command": command, "workdir": workdir}})
log.gateway.error("core.handle: instinct blocked", extra={"_fields": {"instinct": instinct}})
```

Available namespaces (see `_Loggers` in `observability.py`): `log.tool`, `log.gateway`, `log.engine`, `log.scheduler`, `log.notifications`, `log.memory`, `log.parliament`, `log.heartbeat`, `log.cli`, `log.tui`, `log.telegram`, `log.discord`, `log.slack`, `log.whatsapp`, `log.db`, `log.health`, `log.config`, `log.skills`, `log.mcp`, `log.plugins`, `log.security`, `log.tenancy`, `log.infra`, `log.setup`, `log.startup`, `log.integrations`, `log.webhook`, `log.tasks`, `log.owls`.

Logs are written to the single rolling file `~/.stackowl/logs/stackowl.jsonl` (JSONL, one JSON object per line), via a `TimedRotatingFileHandler` that rotates at midnight UTC with `backupCount` from `STACKOWL_LOG_RETAIN_DAYS` (default 30). Rotated backups are renamed `stackowl-YYYY-MM-DD.jsonl` — dated filenames only exist on old rotated files, never on the live one. Path is `StackowlHome.logs_dir()` (`src/stackowl/paths.py`), format is `JsonlFormatter` (`src/stackowl/infra/observability.py`). Records carry snake_case top-level keys: `ts`, `level`, `module`, `msg`, `trace_id`, `span_id`, `parent_span_id`, `session_id`, `duration_ms`, and a `fields` object (the call-site passes `extra={"_fields": {...}}`; the formatter nests it under `"fields"` in the JSONL record).

### Reading Logs

```bash
# All tool calls in the last run
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.fields.tool) | {ts, tool: .fields.tool, args: .fields.args, success: .fields.success, duration_ms}'

# Errors only
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.level == "ERROR") | {ts, module, msg, exc: .fields.exc}'

# Full trace for a specific request (replace trace_id value)
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.trace_id == "TRACE_ID_HERE")'

# Trace a specific tool (e.g., shell)
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.msg | startswith("shell.execute"))'

# Slowest tool calls
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.fields.tool and .duration_ms) | {tool: .fields.tool, duration_ms: .duration_ms}' | sort
```

The AI assistant can also query logs directly via the `read_logs` tool:
```
"What errors happened in the last hour?"
"What did the shell tool receive and return in the last session?"
"Which tools are running slowest?"
```

### Trace Propagation

Every user message mints a UUIDv4 `trace_id` at the channel adapter via `TraceContext.start(...)` (`src/stackowl/infra/trace.py`). This ID propagates through all async hops via `contextvars` — no signature changes required. Every log record written during that request automatically carries the same `trace_id` (read back via `TraceContext.get()` in `JsonlFormatter.format`).

Child spans are created with `async with TraceContext.span("span.name"):`. The tool registry wraps tool execution in a span automatically.

### Per-Tool 4-Point Logging Standard

All tools follow this pattern:

```python
# 1. ENTRY — what came in
log.tool.debug("toolname.execute: entry", extra={"_fields": {**relevant_args}})

# 2. DECISION — which path was chosen and why
log.tool.debug("toolname.execute: using X strategy", extra={"_fields": {"chosen": chosen, "reason": reason}})

# 3. STEP — significant I/O or subprocess
log.tool.debug("toolname.execute: request sent", extra={"_fields": {"status": status, "response_len": response_len}})

# 4. EXIT — what was produced
log.tool.debug("toolname.execute: exit", extra={"_fields": {"success": success, "result_len": result_len, "duration_ms": duration_ms}})

# On any error
log.tool.error("toolname.execute: step failed", exc_info=err, extra={"_fields": {**context}})
```

### Rule: Always Add Logs When Missing

If you encounter a bug or unexpected behavior and the relevant code has no logs, **add them before debugging**. Silent code is undebuggable code. Minimum required for any `execute()` method: entry (inputs) and exit (result/error). Never leave an `except` block empty or silent — always log with `log.<module>.error("op failed", exc_info=err, extra={"_fields": {...context}})`.

### Sensitive Data Rules

- **Never log** raw passwords, generated secrets, or auth tokens
- **Truncate** SQL queries to 200 chars in logs
- **Log URL path only** (strip query strings that may carry API keys): `urlparse(url)._replace(query="", fragment="").geturl()`
- **Log key names only** for MCP tool args (not values)
- **Log prompt length** for image generation (not prompt text)
- The registry auto-redacts args where the key matches `apikey`, `token`, `password`, `secret`, `*_key`, `key_*`, `*token`, `*secret`

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- This graph is scoped to `src/` only (tests/, docs/, _bmad-output/ excluded) and lives at the repo-root `graphify-out/`. **Do not run `graphify hook install` or `graphify update .`/`graphify update src`** — both were tried and rejected: the packaged incremental-update path (`graphify.watch._rebuild_code`'s existing-graph reconciliation) has a reproducible bug that collapses a 12k-node graph to <700 nodes on a single-file change, and the CLI's `update <path>` writes its output under `<path>/graphify-out/` rather than the repo-root one this project uses. There is no safe automatic-update path for this scoping today.
- To refresh the graph after real changes, re-run `/graphify src` (the skill's full pipeline) manually — do not use the `--update` incremental path.
