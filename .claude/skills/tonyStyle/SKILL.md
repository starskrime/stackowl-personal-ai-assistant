---
name: tonyStyle
description: Use whenever touching StackOwl code in this repo — any bugfix, feature, refactor, review, or code read incidental to another task. Proactively scans the whole codebase (not just the current diff) for real defects (silent failures, disabled/stubbed features, architecture violations, missing logging, dead code masking bugs) and fixes them in the same turn with minimal root-cause diffs, gated on tests+lint+type-check staying green and no capability being removed. Composes with systematic-debugging, verification-before-completion, and code-review rather than replacing them — this skill decides *when* those apply even to code nobody named in the request; it delegates the actual debugging/verification mechanics to them.
---

# tonyStyle

## Why this exists

Default posture toward code you didn't write this turn is usually "trust it, only touch what was asked." That posture lets real defects survive indefinitely in code nobody happens to be looking at. This skill flips the default: any StackOwl code you read during a task is guilty until your own reasoning proves it innocent, and confirmed defects get fixed in the same turn — not filed away for later, and never by deleting the feature that was supposedly broken.

This is a lens, not a mechanism. It decides *when* to look and *when* something counts as a real defect. `systematic-debugging` still owns how you debug something you found. `verification-before-completion` still owns how you prove a fix works before claiming success. `code-review`/`bmad-code-review` still own structured review passes. Use this skill to widen what triggers those, not to bypass them.

## Core loop

**1. Scope the task.** State plainly what the user actually asked for. Everything found outside this scope is still in-play (see step 6), but never let scope creep replace the actual ask — deliver that first.

**2. Scan.** While working the task, read task-adjacent code *and* sweep the wider codebase for:
- Silent catches (`except:` / `except Exception:` with no `log.<ns>.error(...)`) — violates this repo's "never catch-and-hide" rule (CLAUDE.md § Rule: Always Add Logs When Missing).
- Disabled or stubbed features (feature flags defaulting off with no active caller, `pass`/`NotImplementedError`/`TODO` sitting in a live code path, dead branches that look like abandoned work).
- Architecture violations against CLAUDE.md's package-responsibility table (e.g. `pipeline/` code doing `owls/`'s job, a channel adapter reaching into `parliament/` directly).
- Missing 4-point logging on `execute()`-style methods (entry/decision/step/exit per CLAUDE.md § Per-Tool 4-Point Logging Standard).
- Dead code that exists specifically to mask a bug (a wrapper that swallows a return value, a retry loop with no backoff limit, a "success" path that never actually checked anything).
- Duplicate subsystems — two or more components independently doing the same job (two daemon-thread pollers, two caches, two code paths reaching the same result) where one could serve both. Architecture-level DRY, not line-level. Be open to consolidating/rearchitecting here same as any other defect — but see step 3's contract check before merging anything.

**3. Static proof-of-innocence.** For each candidate, read the code and trace its callers/call paths before concluding it's actually fine. No test run is required for code the task doesn't otherwise touch — this is a reasoning bar, not an execution bar (keeps whole-codebase scanning affordable). Close an item out only when tracing shows the code is correct, not because it merely looks idiomatic. If reasoning can't close it out cleanly, treat it as ambiguous (see step 6) — never guess and fix anyway.

For duplicate-subsystem candidates specifically: two components that *look* the same are not automatically a merge. Read what each one is independently tested to guarantee (e.g. one component's tests pin down a tick-count debounce, the other's pin down a wall-clock quiet-period — genuinely different contracts wearing the same shape). Merge the parts that are actually identical (shared boilerplate: thread lifecycle, start/stop, loop plumbing) into one implementation; keep the parts that differ for a real, tested reason as the thin, distinct pieces they are. A "simplification" that silently breaks a tested contract to shave lines is a regression, not a fix — main functionality survives the rearchitect, always.

**4. Fix immediately, if real.** Root cause, not the symptom the way `feedback_fix_core_not_patch` frames it — one guard in the shared function beats one guard per caller. Minimal diff per `feedback_minimal_code_changes` — change only the exact lines needed. Never remove, stub, or disable a working capability to make an issue go away (`feedback_never_disable_features`) — a "fix" that deletes the feature is not a fix.

**5. Enhance gate — a fix is not done until all of these pass:**
- `uv run pytest <affected test path(s)>` — green. Never the full suite (`feedback_test_run_discipline` — it hangs on this box); scope to the test file(s) covering the touched code.
- `uv run ruff check src/` — clean on touched files.
- `uv run mypy src/` — clean on touched files.
- Explicit self-check: "did this remove or weaken any existing capability?" — answer must be no.

If a fix can't clear this gate — tests won't go green without breaking something else, or the only way to "resolve" it is to cut a feature — do not ship it. Report it as found-but-unresolved instead (see Error handling below). Shipping a fix that fails its own gate is worse than leaving the defect reported and untouched.

**6. Report.** Deliver the user's actual ask first. Then, if anything was fixed outside that ask, add a short "also found and fixed:" list — one line per fix, file:line, one-sentence defect description. Never fold an out-of-scope fix silently into the diff without mentioning it.

## Error handling

- **Blast radius ceiling:** only fix confirmed defects inside `src/`, `tests/`, or config files. Never destructive git operations (no force-push, no `reset --hard`, no discarding uncommitted work), never unrelated refactors, never touch files outside the repo's own source tree. "Whole codebase, always" describes how far the *scan* reaches, not a license to restructure anything you find along the way.
- **Gate failure:** don't force it green by weakening the check or the fix. Surface the defect as found-but-unresolved, same as any other honest-failure surface in this codebase (`feedback_no_hidden_errors` — never catch-and-hide, that applies to this skill's own failures too).
- **Ambiguous defect vs. deliberate design:** if you can't tell whether something is a bug or an intentional choice, don't guess and fix. Leave it and flag it as a question in the report. This is not "silently punting" (which `feedback_no_deferrals` forbids) — it's surfacing the ambiguity instead of hiding it.

## Self-check (run once after writing/editing this skill file)

Before trusting this skill file, prove the loop actually works on a planted defect:

1. On a throwaway branch, plant one obvious defect unrelated to any current task — e.g. an `except Exception: pass` with no logging in a copy of an existing tool's `execute()`, or comment out a real capability's registration.
2. Start a fresh task that touches a *different* file in the same package (so the planted bug is only found via the wider-codebase scan, not the direct task scope).
3. Confirm: the defect gets found in step 2 of the loop, static tracing in step 3 correctly identifies it as a real defect (not closed out as innocent), it gets fixed in step 4 without removing the underlying feature, the gate in step 5 actually runs the affected test file + ruff + mypy, and the report in step 6 calls it out as "also found and fixed" separate from the task's own deliverable.
4. Discard the throwaway branch — this is a dry run, not a real fix to ship.

If any of these don't happen, the skill file's wording is the bug — revise the relevant section above, not the loop's intent.
