---
name: plan-and-track
description: Use when a goal requires multiple ordered steps that must be decomposed, tracked, and updated as scope changes. Keeps the active plan honest and in sync with actual progress.
when_to_use: When the user asks for a multi-step task (migrate, refactor, set up, investigate) where losing track of completed vs. pending work would cause rework or missed steps.
version: 0.1.0
tags: [planning, tracking, multi-step]
author: stackowl-builtin
license: MIT
---

# Plan and Track Multi-Step Goals

Long tasks fail not because the individual steps are hard but because the plan
drifts from reality — steps are skipped, marked done prematurely, or the scope
changes without the plan reflecting it. This skill enforces a single-source-of-
truth plan that stays in sync with actual work throughout the task.

## Steps

1. **Lay out the ordered subtasks.** Call `update_plan` at the start with the
   full list of steps the task requires. Be specific enough that each step has
   a clear completion criterion, but do not over-plan — add detail as you learn
   more, not upfront.

2. **Mark exactly one step `in_progress` at a time.** Call `todo` to mark the
   current step in progress before starting work on it. Never start a new step
   while a previous one is still marked in progress — finish or explicitly
   abandon it first.

3. **Complete each step before moving to the next.** Only mark a step done after
   verifying its completion (see the `verify-before-claim` skill). Mark it done
   via `todo`, then immediately mark the next step in progress.

4. **Re-plan when scope changes.** If new information changes the remaining
   steps, call `update_plan` again. Record what changed and why — do not silently
   edit a completed step to mean something different.

5. **Report progress in the final reply.** When the task concludes, summarise
   which steps completed, which were skipped (with reason), and whether any are
   still outstanding.

## Verification

Before calling the task done, confirm:

- The plan has no step that is simultaneously `in_progress` and `done`.
- Every step that the user asked for is either `done` or explicitly accounted
  for (skipped with reason, deferred with explanation).
- The plan in the tracking store matches the actual state — if a step was
  completed without being marked done, correct the record before reporting.

## Pitfalls

- **Skipping ahead.** Starting step 3 while step 2 is still `in_progress`
  invalidates the single-in-progress invariant and makes it impossible to
  resume correctly if interrupted.
- **Plan drift.** Completing extra work that was not in the plan without adding
  it as a step means the user has no audit trail of what was done.
- **Over-eager completion.** Marking a step done before verifying its outcome
  produces a plan that looks green but is not. Use `verify-before-claim` at
  each step boundary.
- **Stale plans.** Scope changes that are not reflected in the plan leave future
  steps incoherent. Always call `update_plan` when the goal changes, even if
  only the order of steps shifts.
