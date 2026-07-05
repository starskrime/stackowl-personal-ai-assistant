# Platform Guardian Skill — Design

**Date:** 2026-07-05
**Status:** Approved, pending implementation plan

## Problem

Claude's default posture toward existing StackOwl code is neutral-to-trusting: it fixes what the user names and leaves everything else alone. The user wants an always-on posture shift — any code Claude looks at during a task is treated as potentially wrong until reasoning proves otherwise, and real issues found anywhere in the codebase get fixed in the same turn, not just the file(s) named in the request. Fixes must never trade away functionality or performance for cleanliness — a fix that removes/disables a working capability is not an acceptable fix.

This repo already carries dozens of accumulated feedback-memory rules that partially cover this (`feedback_no_hidden_errors`, `feedback_always_self_healing`, `feedback_check_existing_before_new`, `feedback_minimal_code_changes`, `feedback_never_disable_features`, `feedback_fix_core_not_patch`, `feedback_no_deferrals`, `feedback_4point_logging`, `feedback_test_run_discipline`). No single skill currently operationalizes these into one enforced loop that runs on every code-touching task.

## Goal

A project skill, `platform-guardian`, that:

1. Triggers on any task touching StackOwl source (bugfix, feature, review, or incidental).
2. Scans the whole codebase (not just the current diff) for real defects: silent failures, disabled/stubbed features, architecture violations against `CLAUDE.md`'s package-responsibility table, missing 4-point logging, dead code masking bugs.
3. Applies a "guilty until proven innocent" default: a module is assumed to have a problem until static reading + call-path tracing closes that out. This is a reasoning bar, not a test-running bar — no test execution is required for code the task doesn't otherwise touch.
4. Fixes any confirmed issue immediately, in the same turn, using minimal root-cause diffs — never by removing or disabling a working feature.
5. Gates every fix on: affected `pytest` file(s) green, `ruff check` clean, `mypy` clean on touched files, and an explicit confirmation that no existing capability was removed. A fix isn't "done" until this gate passes.
6. Reports fixes made outside the user's original ask as a short summary appended after the task's own deliverable — never withheld, never silently bundled into an unrelated diff without mention.

## Non-goals

- Not a replacement for `systematic-debugging`, `verification-before-completion`, or `code-review` — Platform Guardian is the always-on lens that decides *when* those apply even to code the user didn't name; it delegates the actual debugging/verification mechanics to them.
- Not a license for destructive git operations, unrelated refactors, or touching files outside `src/`/`tests/`/config.
- Not a mandate to run the full test suite per task — `feedback_test_run_discipline` (full `pytest` hangs on this box) still applies; verification stays scoped to affected files.

## Architecture

Single `SKILL.md` at `.claude/skills/platform-guardian/SKILL.md`, project-scoped (checked into this repo, not global). No new subagent, no new tool — it is a behavioral/process skill in the same family as `systematic-debugging` and `verification-before-completion`, invoked the same way (via the mandatory "if it might apply, invoke it" rule already governing skill use in this environment).

### Core loop

```
1. Scope the task            -> what did the user actually ask for?
2. Scan                      -> read task-adjacent code AND sweep wider codebase
                                 for: silent catches, disabled features,
                                 architecture violations, missing logging,
                                 dead code masking bugs
3. Static proof-of-innocence  -> for each candidate: trace callers/call paths,
                                 check against CLAUDE.md architecture table
                                 and named feedback-memory rules.
                                 Close the item out only if reasoning shows
                                 it's actually correct.
4. Fix (if real)              -> minimal root-cause diff, same turn,
                                 never remove/disable a working feature
5. Enhance gate               -> affected pytest file(s) green
                                 + ruff check clean + mypy clean
                                 + explicit "no capability removed" check
6. Report                     -> user's requested deliverable first,
                                 then a short "also fixed: ..." summary
                                 for anything outside the original ask
```

### Data flow

No new persistent state. The skill reads `CLAUDE.md` (package responsibility table, logging standard) and the memory feedback files listed above as its reference frame each time it runs; it does not maintain its own ledger. (If recurring false-positives or missed patterns emerge, that's future work for a dedicated backlog — not in scope here.)

### Error handling

- If a "fix" can't pass the enhance gate (tests/lint/type-check won't go green, or closing the gap would require removing a feature), the skill does NOT ship a broken/regressive fix — it reports the issue as found-but-unresolved instead, same as any other honest-failure surface in this codebase (`feedback_no_hidden_errors`).
- If scope is ambiguous (is this really a defect, or a deliberate design choice?), default to leaving it and reporting it as a flagged question rather than guessing — consistent with `feedback_no_deferrals`'s spirit of not silently punting, but also not overriding intentional design without cause.

### Testing / verification of the skill itself

Skills are prompt/process artifacts, not code — "testing" here means a self-check demo: a short scenario in the skill file's own documentation (or a one-off manual run against a known small planted issue in a throwaway branch) confirming the loop actually finds a planted silent-catch or disabled-feature bug, fixes it, and gates correctly before reporting. No pytest suite needed for the skill file itself.

## Open items resolved during brainstorming

| Question | Decision |
|---|---|
| Skill vs Agent | Skill |
| Trigger scope | Any code-touching task |
| Blast radius | Whole codebase, always |
| Proof depth | Static read + reasoning (no forced test run on untouched code) |
| Fix vs report | Fix immediately, report after |
| Enhance gate | Tests + lint/type-check green + capability-not-removed check |
