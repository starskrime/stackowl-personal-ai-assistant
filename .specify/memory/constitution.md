# StackOwl Constitution

> StackOwl is a self-hosted personal AI assistant framework ("Jarvis, not a chatbot")
> built around evolving **owl personas**, multi-model routing, parliament debate, and
> durable, verifiable autonomous behavior.

## Version
1.0.0

---

## Context Detection for AI Agents

This file is read by AI agents in two modes:

### 1. Interactive Mode
Outside a Ralph loop, chatting with the user:
- Be conversational; ask clarifying questions when genuinely blocked.
- Research existing code + memory before proposing anything new.
- Discuss architecture, help shape specs and `.ralph/*_IMPLEMENTATION_PLAN.md` files.

### 2. Ralph Loop Mode
Running inside a Ralph bash loop (fed via stdin):
- Fully autonomous — do not ask for permission.
- Read the active plan, pick the **first unchecked** story, implement it completely.
- Verify acceptance criteria with **measured** evidence, not assertions.
- Commit and push. Output `<promise>DONE</promise>` ONLY when 100% complete.
- If criteria not met, fix and try again.

**How to detect:** if the prompt tells you to read a plan and pick a task, you're in Ralph Loop Mode.

---

## Core Principles

### I. Fix the root cause, never the symptom
A report names a symptom. Trace every caller, fix once where all paths route through.
"Pre-existing" is an explanation, not an excuse — never skip pre-existing failures.

### II. No silent failures — fail CLOSED, recover loudly
Never catch-and-hide. Uncertainty fails closed with a durable NACK, never a silent log.
Every `catch` logs with context. No degraded silent fallback. Recover or propagate.

### III. Verify OUTCOMES, not tool names
Success is MEASURED (world-read confirms the effect), never ASSERTED (returncode==0) or
GUESSED (a judge reads a draft). A failed tool or manual hand-off is a give-up, not a win.

### IV. Research before building — registered ≠ reachable
The platform is full of implemented-but-unwired code. grep/read for existing impl first;
wire/extend/finish before writing new. The recurring root cause is "registered but unreachable."

### V. Simplicity & YAGNI
Build exactly what's needed. No premature abstraction, no "just in case." But: enterprise
architecture where it counts — interface-driven, all platforms/edge-cases, structured errors,
fallback chains. Simplicity is the smallest design that meets the real requirement, not a stub.

### VI. Self-healing & self-extending
Every implementation detects failure, resets, and auto-recovers (bounded retry-once); no dead
handles or stuck states. The assistant designs/registers/persists its own new tools and skills.

### VII. Commit small verified successes
Commit at sub-story granularity when green + reviewed. One logical change. Keep the tree bisectable.

### VIII. Finished features ship ON
A completed feature defaults ON, not dormant behind an off flag. An arc is not done until its flag is ON.

---

## Technical Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python ≥3.11 | migrated from `v2/` to repo root; old TS app under `old/` |
| Tooling | `uv` | `uv run python -m stackowl …`, `uv sync` |
| Tests | pytest | run from repo root; **targeted paths + timeout only** (see discipline below) |
| Lint | ruff | `uv run ruff check src/` |
| Types | mypy (strict) | `uv run mypy src/` |
| Storage | SQLite + LanceDB + Kuzu + node-llama-cpp | each one concern; all state under `~/.stackowl/` |
| Channels | Telegram, Slack, CLI/TUI, Web | |

All infra is **self-hosted / open-source** — no vendor lock-in. No vendor names in shipped
`src/` or skills (StackOwl excepted); vendor names live only in research docs.

---

## Project Structure

```
src/stackowl/      # Python package (engine, owls, parliament, pellets, channels, tools, providers)
tests/             # pytest suites — run targeted, never unbounded
.ralph/            # active arc plans, prompts, research, history (Ralph shared state)
specs/             # spec pointers (canonical plans live in .ralph/)
scripts/ralph-loop*.sh   # Ralph loop drivers (Claude/Codex/Gemini/Copilot)
old/               # archived v1 TS app + earlier infra
```

---

## Ralph Wiggum Configuration

### Autonomy Settings
- **YOLO Mode**: ENABLED — `claude --dangerously-skip-permissions` (sandboxed box only)
- **Git Autonomy**: ENABLED — commit + push + merge to main when green.
  - **HARD CONSTRAINT**: NEVER force-push. NEVER `git stash` in a subagent. NEVER rewrite history.
    (A rogue subagent force-push incident is why these are absolute.)

### Work Item Source
- **Primary**: `.ralph/<ARC>_IMPLEMENTATION_PLAN.md` — pick the FIRST unchecked story.
- **Shared cross-iteration memory**: `ralph_history.txt` (append one line per iteration).
- Specs in `specs/` point at the active `.ralph/` plan rather than duplicating it.

### Ralph Loop Scripts (`scripts/`)
```bash
./scripts/ralph-loop.sh        # Claude Code loop, unlimited iterations
./scripts/ralph-loop.sh 12     # cap iterations (controls cost)
./scripts/ralph-loop.sh plan   # planning mode → IMPLEMENTATION_PLAN.md
./scripts/ralph-loop-codex.sh  # Codex variant (also -gemini, -copilot)
```
Each iteration spawns a fresh `claude` process, picks the first unchecked story, implements +
verifies + commits + pushes, marks it done, and exits. The loop ends on the completion promise
or when MAX_ITER is hit. All output is captured under `logs/`.

---

## Development Workflow (per story)

1. Read this constitution + the active `.ralph/` plan; pick the first unchecked story.
2. Check `ralph_history.txt` for prior-iteration learnings/blockers.
3. **Subagent-driven**: delegate impl/QA/tests to subagents; main thread orchestrates + reviews.
4. Write a gateway-driven integration test from the business requirement (mock ONLY the AI provider).
5. Implement completely; add 4-point logging (entry/decision/step/exit) to every `execute()`.
6. **QA + dev review every change** before commit (bugs/edge/security + regression); fix all findings.
7. Verify acceptance criteria with measured evidence. Commit small. Push.
8. Append a concise learning line to `ralph_history.txt`.
9. Output `<promise>DONE</promise>` ONLY when the story is 100% complete; exit for fresh context.

### Completion Signal Rules
- Output `<promise>DONE</promise>` ONLY when acceptance criteria are 100% met and verified.
- The bash loop greps for this exact string; if absent, it iterates again.
- Active arcs may use a scoped promise (e.g. `<promise>ARC-A-PERSISTENCE-COMPLETE</promise>`) —
  emit it ONLY when genuinely true.

---

## Validation Commands

```bash
uv run ruff check src/      # lint
uv run mypy src/            # strict type-check
uv run pytest tests/path/to/test_x.py   # TARGETED tests only
```

**Test-run discipline (Jetson box):** NEVER run full `pytest` unbounded — it hangs.
Always targeted paths with a timeout. Subagents must not `git stash`. Models are remote;
never pull/run a model locally on the Jetson.

---

## Governance

- **Amendments**: edit this file, bump the version, note the change.
- **Compliance**: follow principles in spirit, not just letter.
- **Exceptions**: document and justify any deviation.
- The persistent memory under `~/.claude/.../memory/MEMORY.md` records hard-won root causes —
  treat it as binding context alongside this constitution.

---

**Created**: 2026-06-28
**Version**: 1.0.0
