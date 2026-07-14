---
id: SPEC-epic-execution
companions: ["../../docs/superpowers/specs/2026-07-13-epic-execution-design.md", "../../docs/superpowers/plans/2026-07-13-epic-execution.md"]
sources: []
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. The plan companion already contains the full file list, TDD steps, and code for all 11 stories — each story below implements exactly one Task section from it verbatim. Do not re-derive a different design; follow the plan.

# Dependency-graph-aware epic execution

## Why

Task #4 of the StackOwl coding-capability build plan (see the design companion's Problem section): give the assistant one epic-sized spec, walk away, come back to a verified-done result — without babysitting every story. `ObjectiveDriverHandler` already implements a generic tick/retry/block/notify skeleton but runs every sub-goal strictly sequentially and has no concept of a target repo. Three prerequisite tools (git tool, claude_code worktree isolation, run_tests + TestsPassed) already exist and are registered. The design was brainstormed then adversarially reviewed twice by a BMAD code-review-crew party (security/adversary/edge-case/craftsman/pragmatist) — every finding from both rounds is folded into the design companion and committed.

## Capabilities

- **CAP-1**
  - **intent:** An operator can create a "repo"-bearing objective (an epic) that decomposes into a dependency graph of stories, each running concurrently in an isolated git worktree once its dependencies are done.
  - **success:** `tests/objectives/test_epic_e2e.py`'s 2-story dependency-graph test passes — an independent story and a dependent story both reach `done` (merged into the integration branch) via real driver ticks.
- **CAP-2**
  - **intent:** Creating an epic requires one explicit, informed consent (repo, story count, `bypassPermissions` disclosure) — never a per-story approval.
  - **success:** `tests/tools/scheduling/test_objective_tool.py`'s consent-gate tests pass — no interactive user / no gate wired refuses the epic; the consent summary discloses `bypassPermissions`.
- **CAP-3**
  - **intent:** A failed or blocked story never stalls its independent siblings; the epic only blocks when nothing is progressable.
  - **success:** `tests/objectives/test_driver.py`'s failure-isolation test passes — one story blocked, an unrelated sibling still reaches `done`.
- **CAP-4**
  - **intent:** An operator can confirm a final merge (full or partial) of a completed/stuck epic into its real base branch via `/owls objective-merge <id> YES`.
  - **success:** `tests/commands/test_owls_command.py`'s full-completion and partial-completion merge tests pass.

## Constraints

- Additive only — a plain (non-epic) objective (`repo` unset) must stay byte-identical to today's linear driver behavior at every step.
- Minimal diffs; read every touched file fully before editing (this codebase has fragile history — the memory subsystem alone has 3 documented past incidents).
- Gate every story: targeted `uv run pytest <path>` (never the full suite — it hangs on this box) + `uv run ruff check src/` + `uv run mypy src/` on touched files only.
- Never a silent `except` — always `log.<ns>.error(...)` on any caught exception. 4-point logging (entry/decision/step/exit) on every new `execute()`-shaped method.
- No test mocks `git` itself — every git-touching test uses a real temp repo (matches this codebase's existing `git_tool.py`/`claude_code.py` test style).
- Story dependency order in `stories.yaml` matches the plan's Task 1-11 order exactly — later tasks depend on earlier tasks' deliverables (e.g. Task 7 imports from Task 3's `graph.py` and Task 2's model fields).

## Non-goals

- Automatic merge-conflict resolution (a conflict always escalates to a human).
- Multi-repo epics (one `repo` per objective).
- Fixing the `durable.goals`-off crash-loses-work gap for ephemeral objectives generally (pre-existing, not introduced here).

## Success signal

Running `uv run pytest tests/objectives/ tests/tools/scheduling/ tests/tools/system/ tests/commands/ -q` is fully green, `uv run ruff check src/` and `uv run mypy src/stackowl/objectives/ src/stackowl/tools/scheduling/objective_tool.py src/stackowl/tools/system/git_tool.py src/stackowl/commands/owls_command.py` are both clean, and `tests/objectives/test_epic_e2e.py` demonstrates a real 2-story dependency-graph epic reaching a merged, done state end-to-end.

## Assumptions

- The plan companion's per-task fixture-name placeholders (e.g. "reuse this file's existing `store`/`db_pool` fixture — read the file first") must be resolved by actually reading the referenced test file before writing new tests, per the plan's own instructions — this is not optional.
- `ObjectiveDriverHandler(db=db_pool, backend=None)` is valid for a pure-epic driver instance (Task 11 flags this as needing confirmation against Task 8's actual code before finalizing — confirm during Task 11, do not skip the check).

## Open Questions

- None — both blocking architectural questions from the design review (crash recovery, consent posture) were resolved during brainstorming and are fully specified in the design companion. If a NEW question arises during implementation that isn't answered by the design/plan companions, research it (web search, or by reading more of this codebase) rather than guessing, and note the resolution in the story's own commit message.
