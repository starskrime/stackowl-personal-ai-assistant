# Agent Instructions

## Quick Start

```bash
./scripts/ralph-loop.sh        # Claude Code loop, unlimited iterations
./scripts/ralph-loop.sh 12     # cap iterations (controls cost)
```

Each iteration spawns a fresh `claude` process with a clean context window.

---

## How Ralph Works Here

1. Read `.specify/memory/constitution.md` (principles, stack, hard constraints).
2. Open the active arc plan: `.ralph/<ARC>_IMPLEMENTATION_PLAN.md`.
3. Pick the **first unchecked** story.
4. Check `ralph_history.txt` for prior-iteration learnings/blockers.
5. Implement completely (subagent-driven; QA + dev review before commit).
6. Verify acceptance criteria with **measured** evidence, not assertions.
7. Commit + push. Mark the story done. Append one line to `ralph_history.txt`.
8. Output `<promise>DONE</promise>` (or the arc-scoped promise) ONLY when 100% complete.
9. Exit for fresh context; loop restarts.

---

## Work Item Source

- **Canonical plans live in `.ralph/`**, not `specs/`. `specs/README.md` points at the active plan.
- Active arc: see `.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md` (never-give-up / Arc A).
- Shared cross-iteration memory: `ralph_history.txt`.

---

## Hard Constraints

- NEVER force-push, rewrite history, or `git stash` in a subagent.
- NEVER run full `pytest` unbounded (it hangs) — targeted paths + timeout only.
- Models are remote; never pull/run a model on the Jetson box.
- All runtime state under `~/.stackowl/`, never the project dir.

See `.specify/memory/constitution.md` for the full principle set.
