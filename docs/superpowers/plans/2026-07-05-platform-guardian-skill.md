# Platform Guardian Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `.claude/skills/platform-guardian/SKILL.md`, a project-scoped process skill that makes Claude treat all StackOwl code as guilty-until-proven-innocent on every code-touching task, fix confirmed defects immediately with an enhance-only gate, and report anything fixed outside the user's original ask.

**Architecture:** Single markdown skill file, no new code/subagent/tool. Same family as `systematic-debugging` / `verification-before-completion` — a behavioral lens, not a mechanism. It composes with those skills (and `code-review`) rather than duplicating their internals.

**Tech Stack:** Markdown (Claude Code skill format). No runtime dependency.

## Global Constraints

- Skill file lives at `.claude/skills/platform-guardian/SKILL.md` — project-scoped (checked into this repo), matching the pattern of `.claude/skills/memory/SKILL.md`.
- Frontmatter uses only `name` + `description` (matches the superpowers skill convention already used by `systematic-debugging`, `brainstorming`, `writing-plans` in this environment) — no extra fields.
- The `description` must be broad enough to match the "if 1% chance a skill applies, invoke it" discovery rule this environment already runs on, and must explicitly state how it composes with existing skills instead of replacing them (per spec Non-goals).
- No capability the skill find-and-fixes may ever be removed/disabled to close it out — ties to `feedback_never_disable_features` / `feedback_fix_core_not_patch`.
- Verification commands must match this repo's actual tooling exactly: `uv run pytest <path>`, `uv run ruff check src/`, `uv run mypy src/` (from CLAUDE.md's Commands section).
- Full `pytest` (whole suite) must never be required by this skill — ties to `feedback_test_run_discipline` (hangs on this box).

---

### Task 1: Write the Platform Guardian skill content

**Files:**
- Create: `.claude/skills/platform-guardian/SKILL.md`

**Interfaces:**
- Consumes: nothing (first and only content task)
- Produces: the skill file itself — final deliverable of this plan

- [ ] **Step 1: Write the full skill file**

Create `.claude/skills/platform-guardian/SKILL.md` with this exact content:

````markdown
---
name: platform-guardian
description: Use whenever touching StackOwl code in this repo — any bugfix, feature, refactor, review, or code read incidental to another task. Proactively scans the whole codebase (not just the current diff) for real defects (silent failures, disabled/stubbed features, architecture violations, missing logging, dead code masking bugs) and fixes them in the same turn with minimal root-cause diffs, gated on tests+lint+type-check staying green and no capability being removed. Composes with systematic-debugging, verification-before-completion, and code-review rather than replacing them — this skill decides *when* those apply even to code nobody named in the request; it delegates the actual debugging/verification mechanics to them.
---

# Platform Guardian

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

**3. Static proof-of-innocence.** For each candidate, read the code and trace its callers/call paths before concluding it's actually fine. No test run is required for code the task doesn't otherwise touch — this is a reasoning bar, not an execution bar (keeps whole-codebase scanning affordable). Close an item out only when tracing shows the code is correct, not because it merely looks idiomatic. If reasoning can't close it out cleanly, treat it as ambiguous (see step 6) — never guess and fix anyway.

**4. Fix immediately, if real.** Root cause, not the symptom the way `feedback_fix_core_not_patch` frames it — one guard in the shared function beats one guard per caller. Minimal diff per `feedback_minimal_code_changes` — change only the exact lines needed. Never remove, stub, or disable a working capability to make an issue go away (`feedback_never_disable_features`) — a "fix" that deletes the feature is not a fix.

**5. Enhance gate — a fix is not done until all of these pass:**
- `uv run pytest <affected test path(s)>` — green. Never the full suite (`feedback_test_run_discipline` — it hangs on this box); scope to the test file(s) covering the touched code.
- `uv run ruff check src/` — clean on touched files.
- `uv run mypy src/` — clean on touched files.
- Explicit self-check: "did this remove or weaken any existing capability?" — answer must be no.

If a fix can't clear this gate — tests won't go green without breaking something else, or the only way to "resolve" it is to cut a feature — do not ship it. Report it as found-but-unresolved instead (see Error handling below). Shipping a fix that fails its own gate is worse than leaving the defect reported and untouched.

**6. Report.** Deliver the user's actual ask first. Then, if anything was fixed outside that ask, add a short "also found and fixed:" list — one line per fix, file:line, one-sentence defect description. Never fold an out-of-scope fix silently into the diff without mentioning it.

## Error handling

- **Gate failure:** don't force it green by weakening the check or the fix. Surface the defect as found-but-unresolved, same as any other honest-failure surface in this codebase (`feedback_no_hidden_errors` — never catch-and-hide, that applies to this skill's own failures too).
- **Ambiguous defect vs. deliberate design:** if you can't tell whether something is a bug or an intentional choice, don't guess and fix. Leave it and flag it as a question in the report. This is not "silently punting" (which `feedback_no_deferrals` forbids) — it's surfacing the ambiguity instead of hiding it.

## Self-check (run once after writing/editing this skill file)

Before trusting this skill file, prove the loop actually works on a planted defect:

1. On a throwaway branch, plant one obvious defect unrelated to any current task — e.g. an `except Exception: pass` with no logging in a copy of an existing tool's `execute()`, or comment out a real capability's registration.
2. Start a fresh task that touches a *different* file in the same package (so the planted bug is only found via the wider-codebase scan, not the direct task scope).
3. Confirm: the defect gets found in step 2 of the loop, static tracing in step 3 correctly identifies it as a real defect (not closed out as innocent), it gets fixed in step 4 without removing the underlying feature, the gate in step 5 actually runs the affected test file + ruff + mypy, and the report in step 6 calls it out as "also found and fixed" separate from the task's own deliverable.
4. Discard the throwaway branch — this is a dry run, not a real fix to ship.

If any of these don't happen, the skill file's wording is the bug — revise the relevant section above, not the loop's intent.
````

- [ ] **Step 2: Verify the file is well-formed**

Run:
```bash
uv run python -c "
import yaml, pathlib
text = pathlib.Path('.claude/skills/platform-guardian/SKILL.md').read_text()
assert text.startswith('---\n')
front = text.split('---', 2)[1]
data = yaml.safe_load(front)
assert set(data.keys()) == {'name', 'description'}, data.keys()
assert data['name'] == 'platform-guardian'
assert len(data['description']) > 100
print('OK', data['name'])
"
```
Expected output: `OK platform-guardian`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/platform-guardian/SKILL.md
git commit -m "$(cat <<'EOF'
feat(skills): add platform-guardian process skill

Always-on lens: treat StackOwl code as guilty until proven innocent by
static reasoning, fix confirmed defects in the same turn with a
minimal root-cause diff, gate every fix on tests+ruff+mypy staying
green and no capability being removed, report out-of-scope fixes
after the task's own deliverable. Composes with systematic-debugging,
verification-before-completion, and code-review rather than
replacing them.
EOF
)"
```

---

### Task 2: Self-check the skill against a planted defect

**Files:**
- Create (throwaway, discarded at end of task): a scratch branch off `main`, e.g. `scratch/platform-guardian-selfcheck`
- Modify (on scratch branch only, never merged): one existing tool `execute()` method, e.g. `src/stackowl/tools/meta/owls_list.py`

**Interfaces:**
- Consumes: the finished `SKILL.md` from Task 1
- Produces: a pass/fail confirmation that the skill's own self-check section (documented in Task 1, Step 1) actually holds up in practice — no new production interface

- [ ] **Step 1: Create the scratch branch and plant a defect**

```bash
git checkout -b scratch/platform-guardian-selfcheck
```

In `src/stackowl/tools/meta/owls_list.py`, find the `execute` method and wrap its body in a silent catch to simulate a planted defect:

```python
    async def execute(self, **kwargs: object) -> ToolResult:
        try:
            # ... existing body stays exactly as-is, indented one level ...
            pass
        except Exception:
            pass
```

(Apply this by wrapping the existing method body — do not delete any existing logic, only add the `try`/`except Exception: pass` around it.)

```bash
git add src/stackowl/tools/meta/owls_list.py
git commit -m "test: plant silent-catch defect for platform-guardian self-check"
```

- [ ] **Step 2: Run the self-check scenario**

Start a fresh conversational task in this branch that touches a *different* file in `src/stackowl/tools/meta/` (not `owls_list.py`) — e.g. ask to add a docstring to `src/stackowl/tools/meta/owl_build_existence.py`. Follow the platform-guardian loop from `SKILL.md` while doing it:

- Confirm step 2 (Scan) surfaces the planted `except Exception: pass` in `owls_list.py` during the wider-codebase sweep.
- Confirm step 3 (Static proof-of-innocence) traces it and correctly calls it a real defect, not innocent.
- Confirm step 4 (Fix) removes the silent catch and adds proper logging (`log.tool.error(...)`) without deleting any of the original method body.
- Confirm step 5 (Enhance gate) actually runs `uv run pytest tests/tools/meta/test_owls_list.py`, `uv run ruff check src/stackowl/tools/meta/owls_list.py`, `uv run mypy src/stackowl/tools/meta/owl_build_existence.py src/stackowl/tools/meta/owls_list.py`, and all three pass.
- Confirm step 6 (Report) calls out the `owls_list.py` fix separately from the docstring task, with file:line and a one-sentence description.

Expected: all five checks hold. If any fails, the gap is in `SKILL.md`'s wording, not in this scratch code — go back to Task 1 Step 1 and sharpen the relevant section, then re-run this step.

- [ ] **Step 3: Discard the scratch branch**

```bash
git checkout main
git branch -D scratch/platform-guardian-selfcheck
```

No commit on `main` for this task — it's a dry run of Task 1's deliverable, not new production content.

---

## Self-Review

**Spec coverage:**
- Trigger scope (any code-touching task) → `SKILL.md` frontmatter description + Core loop step 1. ✓
- Blast radius (whole codebase, always) → Core loop step 2. ✓
- Proof depth (static read + reasoning, no forced test run on untouched code) → Core loop step 3. ✓
- Fix vs report (fix immediately, report after) → Core loop steps 4 and 6. ✓
- Enhance gate (tests + lint/type-check + capability-not-removed) → Core loop step 5. ✓
- Composes with systematic-debugging / verification-before-completion / code-review → frontmatter description + "Why this exists" section. ✓
- Named memory feedback rules cited → `feedback_fix_core_not_patch`, `feedback_minimal_code_changes`, `feedback_never_disable_features`, `feedback_test_run_discipline`, `feedback_no_hidden_errors`, `feedback_no_deferrals` all referenced inline. ✓
- CLAUDE.md package-responsibility table + logging standard referenced → Core loop step 2. ✓
- Self-check demo from spec's Testing section → Task 2 in full. ✓

**Placeholder scan:** no TBD/TODO markers; all commands and code blocks are complete and copy-pasteable.

**Type consistency:** N/A — no code interfaces beyond the one skill file; frontmatter keys verified programmatically in Task 1 Step 2.
