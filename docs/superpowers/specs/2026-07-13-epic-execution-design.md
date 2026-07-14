# Dependency-graph-aware epic execution — design

## Problem

The StackOwl coding-capability build plan's Task #4 (see
`[[project_coding_capability_research_2026_07]]` memory): give the
assistant one epic-sized spec, walk away, come back to a verified-done
result — without babysitting every story. `ObjectiveDriverHandler`
(`src/stackowl/objectives/driver.py`) already implements a generic
tick/retry/block/notify skeleton for a multi-step standing objective, but
every sub-goal ("story") runs **strictly sequentially** (`position`
order, one per driver tick) and objectives have no concept of a target
repo at all. Three prerequisite tools now exist to build on: `git`
(`tools/system/git_tool.py`), git-worktree isolation for `claude_code`,
and `run_tests` + the `TestsPassed` post-condition
(`pipeline/acceptance_authority.py`). This design is the fourth piece:
independent stories run concurrently, a blocked story doesn't stall its
unrelated siblings, and worktrees chain off each other's merged results.

## What already exists (do not rebuild)

- `ObjectiveDriverHandler._advance` — the tick/retry(`_MAX_SUBGOAL_ATTEMPTS`)/
  block/notify skeleton this design extends, not replaces.
- `git_tool.add_worktree` / `git_tool.is_git_repo` — module-level helpers
  (extracted during Task #4 prereq #2) already shared with `claude_code.py`'s
  worktree isolation.
- `run_tests` + `TestsPassed` post-condition — the real `verified` signal;
  `aggregate_verdicts` (Phase 1) already combines N tri-state verdicts
  honestly.
- `pipeline/durable/recovery.py`'s `RecoveryDriver` — the exact
  background-task-with-held-strong-ref pattern (`asyncio.create_task` +
  a `set[asyncio.Task]` + a done-callback that discards the ref) this
  design reuses for concurrent story launch, instead of inventing a new
  one.
- `/owls objective-cancel <id> YES` (`commands/owls_command.py`) — the
  confirmed-destructive-action slash-command pattern this design mirrors
  for the epic's final merge confirm.
- `durable.goals` + `DurableTaskRunner`/`DurableTaskStore` — when on, a
  story's run is already a `DurableTask` row; `RecoveryDriver`'s existing
  boot sweep already resumes it after a crash. Nothing new needed there.

## Scope

Additive only. A plain (non-epic) objective — `repo` unset — is
byte-identical to today's linear driver behavior. The epic path activates
only when `Objective.repo` is set.

## Data model changes

**`Objective` gains** (all `None`/default for every existing row):
- `repo: str | None` — target repo path.
- `integration_branch: str | None` — e.g. `stackowl/epic-<objective_id>`,
  branched off `base_branch` when the epic starts.
- `base_branch: str | None` — captured via `git branch --show-current` in
  `repo` at epic creation; what the final merge targets.

**`Subgoal` (a "story") gains:**
- `depends_on: list[str]` — subgoal_ids that must be `done` before this
  one is ready. Empty (default) = ready immediately, matching every
  existing row.
- `worktree_path: str | None`, `story_branch: str | None` — set once the
  story's worktree is created.

No new `SubgoalStatus` value. `done` means "completed **and** merged into
the integration branch" — the merge happens inline at story completion
(see Execution model), so a separate "done-but-unmerged" state is never
observable.

## Creation flow

Extends the existing `ObjectiveTool` with an optional `repo` param — no
new tool. When `repo` is set:
1. Capture `base_branch` (`git branch --show-current` in `repo`).
2. Create `integration_branch` off it.
3. Decomposition uses a graph-aware prompt: each emitted story carries an
   optional `depends_on: list[int]` — indices into the SAME decomposition
   batch (e.g. story 2 depends on `[0]`) — resolved to real `subgoal_id`s
   on insert, mirroring how `position` is assigned by `add_subgoals`
   today. A story with no `depends_on` is ready immediately.

## Execution model

**Readiness.** A story is ready when every id in `depends_on` has
`status == "done"`. The tick loop's per-objective step changes from
"find the one next-position pending subgoal" to "find every ready
pending subgoal" (a pure function: stories + `depends_on` → ready set,
independently unit-testable with no DB).

**Launch.** Each newly-ready story of an epic objective is launched as a
background `asyncio.Task`, held in a strong-ref set exactly like
`RecoveryDriver._drives` — the driver tick returns immediately instead of
blocking on it (a `claude_code` call can run up to 30 minutes; the
scheduler tick cadence is ~1 minute). The story's status flips to
`running` the moment it's launched, so the next tick never double-launches
it — it only checks whether that status is still `running` or has reached
a terminal state.

**Per-story background sequence:**
1. Create a worktree off the **current tip of the integration branch**
   (`git_tool.add_worktree`).
2. Run `claude_code` in it.
3. Run `run_tests`; read the `TestsPassed` verdict.
4. On success, merge the story branch into the integration branch
   **inline**, under a per-objective `asyncio.Lock` (an in-process dict
   keyed by `objective_id`) so two stories finishing simultaneously don't
   race the same merge target.
   - Clean merge → `status="done"`.
   - Merge **conflict** → escalate that story to `blocked`/`decision`
     (no auto-conflict-resolution — matches the existing
     ask-on-irreversible posture; the work is preserved on its branch,
     not discarded).
5. On failure (bad edit, or tests never pass after the existing
   `_MAX_SUBGOAL_ATTEMPTS` retry budget) → escalate only that story to
   `blocked`, exactly today's per-subgoal logic.

**Failure isolation.** A failed story's **dependents** simply never
become ready — they stay `pending` (honest: they genuinely can't
proceed). Everything not downstream of the failure keeps launching
normally. The **objective** itself flips to `blocked` only when nothing
is progressable at all — no ready stories, not everything done, and at
least one unresolved failure — replacing today's "any single sub-goal
failure blocks the whole objective."

**Crash recovery.** Reuses `RecoveryDriver`'s existing boot-time orphan
sweep for the underlying `DurableTask` when `durable.goals` is on; a
restarted tick treats a `running` story as "poll its durable status, don't
relaunch." With `durable.goals` off, a crash loses an in-flight story
exactly as it loses any in-flight ephemeral objective sub-goal today —
not a new gap this design introduces.

## Final merge confirm

When every story reaches `done` (merged into the integration branch), the
epic does **not** auto-finish like a plain objective does today. It
transitions to `blocked`/`decision` with a message like *"Epic complete —
6/6 stories verified and merged into `stackowl/epic-obj-abc123`. Reply
`/owls objective-merge obj-abc123 YES` to merge into `main`."* — reusing
the existing blocked+notify pathway; no new `Objective` status.

**New slash command `objective-merge <id> YES`** mirrors
`objective-cancel <id> YES` exactly (same confirm-with-YES pattern, same
command module). It validates the objective is specifically in the
ready-to-merge sub-case (status `blocked`, `integration_branch` set, every
story `done`) before acting — a failure-blocked epic (stuck on a
genuinely failed story) refuses this command with a clear "not ready"
message; only `objective-cancel` or manual intervention applies there. On
success it merges `integration_branch` into `base_branch` in `repo` and
flips the objective to `done`.

## Testing

`tests/objectives/test_driver.py` already tests the driver against a fake
backend. Extends that same harness for: concurrent launch of independent
stories, a failed story not blocking unrelated siblings, dependents never
becoming ready after a dependency fails, and the objective-level "only
blocked when nothing progressable" transition. The readiness function
itself gets plain unit tests (no DB/backend). The merge-conflict
escalation path gets a **real-git** test (real temp repos, mirroring
`git_tool.py`'s and `claude_code.py`'s own test style) since that's
exactly the kind of behavior a mock would rubber-stamp incorrectly.

## Explicitly out of scope

- Automatic merge-conflict resolution.
- Multi-repo epics (one `repo` per objective).
- Per-story human confirm (only the final merge asks).
- Fixing the `durable.goals`-off crash-loses-work gap for ephemeral
  objectives generally (pre-existing, not introduced here).
