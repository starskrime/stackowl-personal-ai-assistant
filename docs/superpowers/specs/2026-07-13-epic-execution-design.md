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

**Revision note (this version):** a BMAD code-review-crew adversarial pass
(5 independent reviewers: security, correctness/crash, edge-case,
architecture-reuse, pragmatist) found the first draft asserted two things
it hadn't actually verified — that `RecoveryDriver`'s existing boot sweep
covers worktree/git state (it doesn't — it only resumes conversation
state) and that removing per-story consent was a "consistent extension"
of existing behavior (it isn't — it removes the one gate,
`_park_is_irreversible`, that today keeps a consequential tool from ever
running unattended without a human's yes). Both are resolved below,
along with 7 smaller confirmed fixes.

**Revision note (round 2):** the same crew re-reviewed this revision.
Verdict split: crash-recovery and lock-scoping mechanics now hold up
under adversarial replay (credited explicitly). But the stated consent
mechanism ("`ObjectiveTool`'s `action_severity` becomes `consequential`
when `repo` is set") doesn't work — `manifest` is a zero-arg property
read before any call's args exist (`pipeline/steps/execute.py:1299`),
so there is no seam in this codebase for per-call-conditional severity.
Fixed below by mirroring `shell.py`'s `_gate_catastrophic()` instead — a
proven pattern for "a normally-non-consequential tool forces consent for
a specific subset of its own calls" without varying the manifest. Also
fixed: the cycle-detection DFS needed to name on-stack marking
explicitly (a flat visited set false-rejects legitimate diamond
dependencies), worktree cleanup had no trigger at all, and two mechanics
were correct but silently undocumented (recovery's tick cadence, and
that post-merge re-testing only catches what the suite covers, not a
guarantee). Six more small fixes below.

## What already exists (do not rebuild)

- `ObjectiveDriverHandler._advance` — the tick/retry(`_MAX_SUBGOAL_ATTEMPTS`)/
  block/notify skeleton this design extends, not replaces.
- `git_tool.add_worktree` / `git_tool.is_git_repo` / `git_tool`'s `status`
  operation — module-level helpers (extracted during Task #4 prereq #2)
  already shared with `claude_code.py`'s worktree isolation, and reused
  again here for the crash-recovery git-status check (see below).
- `run_tests` + `TestsPassed` post-condition — the real `verified` signal;
  `aggregate_verdicts` (Phase 1) already combines N tri-state verdicts
  honestly.
- `pipeline/durable/recovery.py`'s `RecoveryDriver` — the exact
  background-task-with-held-strong-ref pattern (`asyncio.create_task` +
  a `set[asyncio.Task]` + a done-callback that discards the ref) this
  design reuses for concurrent story launch. **Scope correction:** its
  boot sweep resumes a `DurableTask`'s `PipelineState` (messages, tool
  calls, iteration count) only — it has zero awareness of a worktree
  directory or git branch. It is reused for what it actually does
  (conversation-level orphan recovery); worktree-level orphan recovery is
  new logic in this design (see Crash recovery below), not something
  inherited for free.
- `TurnRegistry.session_intake_lock` (`turn_registry.py:205-215`) — the
  existing precedent for a keyed, lazily-created, per-key `asyncio.Lock`
  in a `dict`. The epic's merge lock mirrors this pattern exactly rather
  than inventing a new locking idiom.
- `/owls objective-cancel <id> YES` (`commands/owls_command.py`) — the
  confirmed-destructive-action slash-command pattern this design mirrors
  for the epic's final merge confirm.
- `execute_code`'s per-call `consent_summary()` — the pattern the epic's
  own consent prompt's CONTENT mirrors (see Creation flow).
- `shell.py`'s `_gate_catastrophic()` — the pattern the epic's consent
  MECHANISM mirrors: a tool with a normally-fixed, non-consequential
  `manifest.action_severity` calls the consent gate directly from inside
  `execute()` for the specific calls that need it, instead of trying to
  vary the static manifest per call (which the tool ABC has no seam for).
- `delegation_profile="autonomous"` (Phase 0) — the precedent for an
  explicit, *disclosed* autonomy tier rather than a silent gate removal;
  the epic's consent posture follows the same principle.

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
new tool, and `ObjectiveTool.manifest.action_severity` stays `"write"`
unconditionally (plain objectives are completely unaffected — no
per-call manifest variance, which the tool ABC has no seam for anyway).
When `repo` is set, `execute()` calls the consent gate **directly**,
mirroring `shell.py`'s `_gate_catastrophic()`: it builds a summary
disclosing the repo path, the estimated story count from decomposition,
and — load-bearing, see Consent posture below — that stories run
unattended with `permission_mode="bypassPermissions"`, then requests
consent through the same policy `execute_code`/`claude_code` use. No
interactive user / no gate wired / declined → the epic is refused
outright, fail-closed, same as every other consequential path in this
codebase — never created and never silently downgraded to non-consequential.

1. Capture `base_branch` (`git branch --show-current` in `repo`).
2. Create `integration_branch` off it.
3. Decomposition uses a graph-aware prompt: each emitted story carries an
   optional `depends_on: list[int]` — indices into the SAME decomposition
   batch (e.g. story 2 depends on `[0]`) — resolved to real `subgoal_id`s
   on insert, mirroring how `position` is assigned by `add_subgoals`
   today. A story with no `depends_on` is ready immediately.
4. **Graph validation before any subgoal is persisted:** a DFS over the
   emitted `depends_on` indices, using **on-stack marking (three-color:
   white/gray/black)**, not a flat global-visited set — a flat-visited
   check would false-reject a legitimate diamond dependency (e.g. D
   depending on both B and C, which both depend on A: A is visited twice
   via two different, valid paths, not revisited via a back-edge). Only
   a node revisited **while still on the current recursion stack** is a
   real cycle. The same pass checks for an out-of-range index. Either
   failure mode fails epic creation outright with a clear validation
   error — the epic never starts, rather than starting and silently
   hanging on a cyclic or dangling dependency. (An adversarial review
   found the original "let it degrade into the existing blocked path"
   plan doesn't actually work: a pure cycle produces zero subgoal
   failures, and the objective-blocked condition requires at least
   one — so it would have hung forever, `active`, with no notification.
   Validating up front avoids the case entirely.)

## Consent posture

Creating a `repo`-bearing objective is the **one** consent point for the
epic's entire unattended run — there is no per-story confirm (mechanism:
see Creation flow's `shell._gate_catastrophic()`-style direct gate call).
This mirrors `delegation_profile="autonomous"` (Phase 0): an explicit,
disclosed autonomy tier, not a silent removal of the gate every other
objective sub-goal relies on (`_park_is_irreversible` — a consequential
tool like `claude_code` can't get consent in a non-interactive context,
so today it parks and blocks until a human approves; that mechanism
still applies to a *plain* objective's sub-goals, and still means at
most one such call is ever in flight for those).

Repo path confinement (jailing/allowlisting where `repo` may point) is
**not** addressed here — checked against `shell`/`execute_code`/
`claude_code`, none of which confine their target paths either; this
platform's existing trust model is consent, not confinement, and an
epic pointed at an unwise `repo` is the same class of risk as any of
those tools pointed at one, not a new exposure this design introduces.

Story launches run with `permission_mode="bypassPermissions"`, not the
tool's own default `acceptEdits`. Checked directly: `acceptEdits` only
auto-approves file edits — the first shell command a story needs (installing
a dependency, running a build step) would hit an approval prompt with no
TTY to answer it, hanging exactly like `default` mode's documented danger.
Genuinely unattended coding work needs shell access. That is real exposure
beyond file edits (worktree isolation contains file changes, not shell
side effects like network calls or global installs) — the design doesn't
shrink that exposure, it makes the single consent point say so explicitly,
so the epic's one YES is informed rather than vague.

## Execution model

Implemented as a branch inside `_advance` (`if objective.repo:` dispatches
to new dedicated epic-path methods) — one entry point, not a parallel
`_advance_epic` sibling reimplementing the retry/block/notify skeleton.

**Readiness.** A story is ready when every id in `depends_on` has
`status == "done"`. The tick loop's per-objective step changes from
"find the one next-position pending subgoal" to "find every ready
pending subgoal" (a pure function: stories + `depends_on` → ready set,
independently unit-testable with no DB).

**Launch — explicit synchronization point.** For each newly-ready story,
the tick (1) `await`s a single DB write marking that story `running` —
this call completes, and the tick function does not return, until it has
— then (2) calls `asyncio.create_task(...)` for the story's actual work
and adds it to a strong-ref set exactly like `RecoveryDriver._drives`.
Step 1 happening-and-completing strictly before the tick returns is what
prevents a double-launch: the scheduler only considers this job's
execution finished (and eligible to fire again) once `execute()` returns,
so by the time that happens every newly-launched story is already
durably `running` in the DB — the next tick's readiness scan will see it
correctly. (An adversarial review specifically flagged this as an
asserted-not-demonstrated atomicity claim in the prior draft; this is the
concrete mechanism, not a restated assertion.)

**Per-story background sequence:**
1. Re-validate `is_git_repo(repo)` (the epic's consent was given at
   creation time, possibly hours before a given story launches — a moved
   mount or a swapped symlink means the path consented to may no longer
   be what it was; a launch that fails this check escalates the story to
   `blocked` rather than operating unattended against an unverified
   path), then create a worktree off the **current tip of the
   integration branch** (`git_tool.add_worktree`).
2. Run `claude_code` in it (`permission_mode="bypassPermissions"` — see
   Consent posture).
3. Run `run_tests`; read the `TestsPassed` verdict.
4. On success, merge the story branch into the integration branch
   **inline**, under a lock keyed by **`repo` path** (not `objective_id`
   — two epics pointed at the same on-disk repo would otherwise hold
   independent, uncontended locks while racing the same physical git
   directory) in an in-process `dict[str, asyncio.Lock]`, mirroring
   `TurnRegistry.session_intake_lock` exactly.
   - Clean merge → run `run_tests` again, this time **against the
     integration branch itself** (not just the story's own worktree) —
     two independently-passing stories are not proof their union
     works. Integration tests pass → `status="done"`; the story's
     worktree is removed (`git_tool`'s worktree_remove) since its
     content now lives in the integration branch — the *branch* itself
     is kept (cheap, inspectable) even though the working directory is
     cleaned up. Integration tests fail → the merge is **kept** (never
     auto-revert — same no-auto-resolution principle as a conflict) but
     the story escalates to `blocked`/`decision` with the integration
     failure surfaced, since its own merge was clean but the combined
     result wasn't verified. **Named limitation:** re-testing after
     every merge means every later merge re-validates every earlier
     one's assumptions *as far as the suite has coverage for them* — a
     signal, not a guarantee; a gap in the suite itself can still let a
     bad interaction through undetected. Not fixable by this design (no
     design closes an incomplete test suite) — named as a documented
     risk instead of silently implied-solved.
   - Merge **conflict** → escalate that story to `blocked`/`decision`
     (no auto-conflict-resolution — matches the existing
     ask-on-irreversible posture; the work is preserved on its branch,
     the worktree is left in place for inspection, not discarded).
5. On failure (bad edit, or tests never pass after the existing
   `_MAX_SUBGOAL_ATTEMPTS` retry budget) → escalate only that story to
   `blocked`, exactly today's per-subgoal logic. Worktree left in place
   for inspection (not cleaned up — only the clean-merge path removes it).

**Failure isolation.** A failed story's **dependents** simply never
become ready — they stay `pending` (honest: they genuinely can't
proceed). Everything not downstream of the failure keeps launching
normally. The **objective** itself flips to `blocked` only when nothing
is progressable at all — no ready stories, not everything done, and at
least one unresolved failure — replacing today's "any single sub-goal
failure blocks the whole objective."

**Crash recovery — worktree-aware, not inherited for free.** A tick
identifies an orphaned story precisely: `status == "running"` **and its
subgoal_id is not in this process's local live-task set** (the exact
definition `RecoveryDriver` already uses for a `DurableTask`, applied one
layer up for the worktree-specific state it doesn't cover). This check
runs on the driver's **normal tick cadence** — there is no separate boot
sweep to build or bound; the very first tick after a restart already
performs it, same as every other readiness scan. It also catches a
second, distinct case for free: a story that ended up `running` in the
DB with no task ever actually backing it (e.g. an exception landed
between the DB write and the `create_task` call) is indistinguishable
from — and handled identically to — a genuine crash orphan, with no
special-casing needed. For each orphan, run `git_tool`'s `status`
operation against its worktree before touching it further:
- **Clean tree** (fully committed, nothing dirty, no mid-merge state) →
  safe to discard and restart the story fresh in a **new** worktree —
  nothing uncommitted to lose. Counts against the existing
  `_MAX_SUBGOAL_ATTEMPTS` budget.
- **Dirty or mid-merge** → escalate to `blocked`/`decision`, with the
  actual `git status` output attached — never auto-resume or
  auto-discard state nobody verified (the same principle
  `AcceptanceAuthority`/`TestsPassed` exists to enforce elsewhere).

With `durable.goals` off, a crash loses an in-flight story exactly as it
loses any in-flight ephemeral objective sub-goal today — a pre-existing
gap, not a new one.

**Worktree lifecycle.** Beyond the clean-merge auto-cleanup above, a
`blocked` story's worktree is deliberately kept for inspection — but not
forever. `objective-cancel` (abandoning the whole epic) removes every
worktree recorded across all of the epic's stories, done or not. A
*partial* `objective-merge` (see Final merge confirm) removes the
worktrees of every story being dropped as part of that confirm — only a
fully-`done` epic has none left to clean up by construction.

## Final merge confirm

When every story reaches `done` (merged into the integration branch), the
epic does **not** auto-finish like a plain objective does today. It
transitions to `blocked`/`decision` with a message like *"Epic complete —
6/6 stories verified and merged into `stackowl/epic-obj-abc123`. Reply
`/owls objective-merge obj-abc123 YES` to merge into `main`."* — reusing
the existing blocked+notify pathway; no new `Objective` status.

**Partial completion.** When the objective blocks because nothing is
progressable (per Failure isolation above) but at least one story
reached `done`, the notify message reflects that instead:
*"Epic stuck — 4/6 stories done and merged into
`stackowl/epic-obj-abc123`; 2 permanently blocked. Reply
`objective-merge obj-abc123 YES` to merge the 4 completed stories, or
`objective-cancel` to abandon."* followed by **one line per non-done
story naming its own reason** — a directly-blocked story shows its
failure/conflict text; a story that's merely stuck behind a blocked
dependency shows that explicitly (e.g. `sub-c: blocked because
dependency sub-b is stuck`), walking the dependency chain rather than
collapsing every non-done story into a bare count. `objective-merge`
accepts both the fully-done and the partial case — it only refuses when
*zero* stories are done (nothing to merge).

**New slash command `objective-merge <id> YES`** mirrors
`objective-cancel <id> YES` exactly (same confirm-with-YES pattern, same
command module). It validates the objective is in a merge-eligible
`blocked` state (`integration_branch` set, at least one story `done`)
before acting. On success it merges `integration_branch` into
`base_branch` in `repo` and flips the objective to `done`. If *that*
merge conflicts (rare — `base_branch` may have moved since the epic
started), the command surfaces the git error and leaves the objective
blocked; unlike a per-story merge this one is a synchronous command an
operator is watching, so a bare failure is self-evident and needs no
special handling.

## Testing

`tests/objectives/test_driver.py` already tests the driver against a fake
backend. Extends that same harness for: concurrent launch of independent
stories, a failed story not blocking unrelated siblings, dependents never
becoming ready after a dependency fails, the objective-level "only
blocked when nothing progressable" transition, and the partial-completion
notify path (including the per-story reason lines, not just the count).
The readiness function and the cycle/range validator get plain unit
tests (no DB/backend) — including a **diamond-shaped** acyclic graph
(D depends on B and C, both depend on A) to prove the validator uses
on-stack marking and doesn't false-reject legitimate fan-in. The
merge-conflict escalation, the post-merge integration-test-failure
escalation, the crash-recovery git-status branch (clean → restart,
dirty → escalate), and the dropped-launch orphan case (a story marked
`running` with no backing task, no crash involved) all get **real-git**
tests (real temp repos, mirroring `git_tool.py`'s and `claude_code.py`'s
own test style) since these are exactly the behaviors a mock would
rubber-stamp incorrectly. Worktree cleanup gets a test covering both
`objective-cancel` (all worktrees removed) and a partial
`objective-merge` (only the dropped stories' worktrees removed). The
consent-gate change gets a test confirming a `repo`-bearing
`ObjectiveTool.execute()` call actually reaches the consent gate (not
just that a manifest field looks right) and that its summary shows repo
+ story-count + `bypassPermissions`.

## Explicitly out of scope

- Automatic merge-conflict resolution.
- Multi-repo epics (one `repo` per objective).
- Per-story human confirm (only the final merge asks — the epic-creation
  consent covers the whole run, see Consent posture).
- Fixing the `durable.goals`-off crash-loses-work gap for ephemeral
  objectives generally (pre-existing, not introduced here).
