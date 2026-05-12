# Cycle 3 Stability Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock down the foundation before Cycle 4 — fix 6 pre-existing test failures, migrate 35 direct `os.tmpdir()`/`os.homedir()`/`process.platform` call sites across 16 files to `platform.*`, bump ESLint guardrail to `error`, set up CI matrix on ubuntu/macos/windows.

**Architecture:** Four sequential phases — A (fix tests, investigative one-per-file) → B (mechanical batched migration) → C (one-line ESLint config change) → D (new GitHub Actions workflow). Phase C depends on B's grep gate hitting zero; Phase D runs after A so CI lands green.

**Tech Stack:** TypeScript strict, Vitest, ESLint flat config, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-11-cycle-3-stability-design.md`

---

## File Map

```
__tests__/ambient.test.ts                              # MODIFY/DELETE — top-level FAIL
__tests__/skills-installer.test.ts                     # MODIFY — fromLocal test
__tests__/pellets/pellet-retriever.test.ts             # MODIFY — default config
__tests__/skills/skill-usage-sqlite.test.ts            # MODIFY — v29 schema test
__tests__/cli/v2/commands/registry.test.ts             # MODIFY — /exit alias
__tests__/cli/v2/state/panel.test.ts                   # MODIFY — panelStack init

src/                                                   # MIGRATIONS — 16 files
├── index.ts                                           # 8+ usages
├── tools/registry.ts                                  # platform field
├── tools/screenshot.ts
├── tools/macos/clipboard.ts
├── tools/macos/text-to-speech.ts
├── tools/computer-use/driver/manager.ts
├── tools/live-browser/frontmost.ts
├── evolution/synthesizer.ts
├── voice/adapter.ts
├── skills/loader.ts
├── engine/runtime.ts
├── skills/registry.ts
├── signals/collectors.ts
├── browser/puppeteer-fetcher.ts
├── gateway/adapters/voice.ts
└── swarm/node.ts

eslint.config.js                                       # MODIFY — warn → error
.github/workflows/test.yml                             # NEW — CI matrix
```

---

# Phase A — Fix 6 pre-existing test failures

## Task 1: Fix `__tests__/ambient.test.ts`

**Files:**
- Modify or delete: `__tests__/ambient.test.ts`

**Symptom:** test file fails to load at all (top-level FAIL, not a test inside it).

- [ ] **Step 1: Diagnose**

```bash
npx vitest run __tests__/ambient.test.ts 2>&1 | head -40
```

Read the actual error — could be import-resolution, syntax error, or missing module.

- [ ] **Step 2: Read the test**

```bash
cat __tests__/ambient.test.ts
```

Identify what behavior it tests, then read the corresponding source under `src/`.

- [ ] **Step 3: Decide**

- If the test references a symbol/module that was renamed/moved: update the import.
- If the test references a feature that was removed: delete the test (with a commit message linking the prior commit that removed the feature).
- If a real bug exists in the code under test: fix the code.

- [ ] **Step 4: Run**

```bash
npx vitest run __tests__/ambient.test.ts 2>&1 | tail -5
```

Expected: green or file gone.

- [ ] **Step 5: Commit**

```bash
git add __tests__/ambient.test.ts <any src/ files touched>
git commit -m "fix(tests): repair ambient.test.ts — <one-line reason>"
```

(Or `git rm __tests__/ambient.test.ts && git commit -m "chore(tests): delete obsolete ambient.test.ts — feature removed in <commit>"`.)

---

## Task 2: Fix `__tests__/skills-installer.test.ts > fromLocal`

**Files:**
- Modify: `__tests__/skills-installer.test.ts` and/or `src/skills/installer.ts`

**Symptom:** `SkillInstaller.fromLocal > copies SKILL.md from local path to target dir` fails.

- [ ] **Step 1: Diagnose**

```bash
npx vitest run __tests__/skills-installer.test.ts -t "fromLocal" 2>&1 | tail -20
```

- [ ] **Step 2: Read both ends**

```bash
sed -n '1,40p' __tests__/skills-installer.test.ts
grep -n "fromLocal" src/skills/installer.ts | head -5
```

Look for: did the sanitizer (Epic 1) change the destination path? Does the test assert the file at the old path?

- [ ] **Step 3: Decide + fix**

- If the destination path moved: update the assertion.
- If the copy logic is genuinely broken: fix `src/skills/installer.ts`.

- [ ] **Step 4: Run**

```bash
npx vitest run __tests__/skills-installer.test.ts 2>&1 | tail -5
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit**

```bash
git add __tests__/skills-installer.test.ts src/skills/installer.ts
git commit -m "fix(tests): skills-installer.fromLocal — <one-line reason>"
```

---

## Task 3: Fix `__tests__/pellets/pellet-retriever.test.ts > default config`

**Files:**
- Modify: `__tests__/pellets/pellet-retriever.test.ts` and/or `src/pellets/retriever.ts` (or wherever PelletRetriever lives)

**Symptom:** `PelletRetriever > retrieveRelevant > should use default config values`.

- [ ] **Step 1: Diagnose**

```bash
npx vitest run __tests__/pellets/pellet-retriever.test.ts -t "default config" 2>&1 | tail -20
```

- [ ] **Step 2: Read**

```bash
grep -n "default config" __tests__/pellets/pellet-retriever.test.ts
find src/pellets -name "*.ts" | xargs grep -l "PelletRetriever\|retrieveRelevant" | head -5
```

Compare the expected default values in the test against what the source actually provides.

- [ ] **Step 3: Decide + fix**

- If the test's expected values are stale (defaults changed intentionally elsewhere): update test.
- If the source lost its defaults (regression): restore them.

- [ ] **Step 4-5: Run + commit**

```bash
npx vitest run __tests__/pellets/pellet-retriever.test.ts 2>&1 | tail -5
git add <files>
git commit -m "fix(tests): pellet-retriever default config — <one-line reason>"
```

---

## Task 4: Fix `__tests__/skills/skill-usage-sqlite.test.ts > creates skill_usage table at v29`

**Files:**
- Modify: `__tests__/skills/skill-usage-sqlite.test.ts`

**Symptom:** test asserts schema state at db version 29; the db is now past v29 so the migration code-path the test exercises may be different.

- [ ] **Step 1: Diagnose**

```bash
npx vitest run __tests__/skills/skill-usage-sqlite.test.ts 2>&1 | tail -20
grep -n "skill_usage\|user_version" src/memory/db.ts | head -10
```

- [ ] **Step 2: Decide**

This is almost certainly a test-side fix: assert against the current schema version, not a frozen v29. Update the version expectations or the migration trigger setup so the test exercises the right code path. If the test is now redundant (subsumed by a newer migration test), delete it.

- [ ] **Step 3: Fix**

Update the test to assert against current schema. Use whatever the current `user_version` PRAGMA returns after fresh `MemoryDatabase()` construction.

- [ ] **Step 4-5: Run + commit**

```bash
npx vitest run __tests__/skills/skill-usage-sqlite.test.ts 2>&1 | tail -5
git add __tests__/skills/skill-usage-sqlite.test.ts
git commit -m "fix(tests): skill-usage-sqlite — assert current schema version, not frozen v29"
```

---

## Task 5: Fix `__tests__/cli/v2/commands/registry.test.ts > /exit alias`

**Files:**
- Modify: `__tests__/cli/v2/commands/registry.test.ts` and/or `src/cli/v2/commands/registry.ts`

**Symptom:** `REGISTRY > resolves /exit as alias for /quit` — the alias is missing.

- [ ] **Step 1: Diagnose**

```bash
grep -n "exit\|quit" src/cli/v2/commands/registry.ts | head -10
grep -n "exit\|quit" __tests__/cli/v2/commands/registry.test.ts | head -10
```

- [ ] **Step 2: Decide + fix**

- If `/exit` was intentionally removed (e.g., to consolidate on `/quit`): delete the alias-test.
- If it was accidentally removed: restore the alias in the registry source.

- [ ] **Step 3-4: Run**

```bash
npx vitest run __tests__/cli/v2/commands/registry.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add __tests__/cli/v2/commands/registry.test.ts src/cli/v2/commands/registry.ts
git commit -m "fix(cli): restore /exit as alias for /quit" # or: chore(tests): drop obsolete /exit alias test
```

---

## Task 6: Fix `__tests__/cli/v2/state/panel.test.ts > panelStack iterable`

**Files:**
- Modify: `__tests__/cli/v2/state/panel.test.ts` or `src/cli/v2/state/slices/panel.ts`

**Symptom:** Two tests fail with `TypeError: state.panelStack is not iterable` at line 25 of `panel.ts` (`const newStack = [...state.panelStack, next];`).

- [ ] **Step 1: Diagnose**

```bash
sed -n '1,40p' src/cli/v2/state/slices/panel.ts
sed -n '1,50p' __tests__/cli/v2/state/panel.test.ts
```

The reducer assumes `state.panelStack` is an array. The tests pass a `state` without `panelStack`. Either the test fixture is wrong OR the reducer needs to defensively initialize.

- [ ] **Step 2: Decide**

Prefer fixing the reducer to be robust against partial state (defensive init: `const stack = state.panelStack ?? [];`). Tests can pass partial state for focused testing — that's normal in slice tests.

- [ ] **Step 3: Fix**

In `src/cli/v2/state/slices/panel.ts` line ~25:

```typescript
// Before
const newStack = [...state.panelStack, next];

// After
const newStack = [...(state.panelStack ?? []), next];
```

Check other places in the file that touch `state.panelStack` — apply the same defensive `?? []` pattern.

- [ ] **Step 4: Run**

```bash
npx vitest run __tests__/cli/v2/state/panel.test.ts 2>&1 | tail -5
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/state/slices/panel.ts
git commit -m "fix(cli): defensive init for panelStack in applyPanelEvent reducer"
```

---

# Phase B — Migrate 35 call sites to `platform.*`

**Mapping table (applies to every Phase B task):**

| Find | Replace with |
|---|---|
| `os.tmpdir()` | `platform.paths.tempdir()` |
| `os.homedir()` | `platform.paths.home()` |
| `process.platform === "darwin"` | `platform.systemInfo.current().platform === "darwin"` |
| `process.platform === "linux"` | `platform.systemInfo.current().platform === "linux"` |
| `process.platform === "win32"` | `platform.systemInfo.current().platform === "win32"` |
| `process.platform` (other reads) | `platform.systemInfo.current().platform` |

For each modified file: add `import { platform } from "<relative path to src/platform/index.js>";` if not already present. Determine the correct relative path based on the file's location.

**Per-task verification (run after every batch):**
```bash
npm run build 2>&1 | grep "error TS" | wc -l        # must be 0
```

**Final Phase B gate (after Task 11):**
```bash
grep -rn "os\.tmpdir()\|os\.homedir()\|process\.platform" src/ --include="*.ts" | grep -v "src/platform/"
```
Expected: zero results.

---

## Task 7: Batch 1 — `src/index.ts` (heaviest hitter)

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Enumerate every usage in this file**

```bash
grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" src/index.ts
```

Note each line number. There are 8+ usages.

- [ ] **Step 2: Add platform import if missing**

Check existing imports at the top:
```bash
grep -n "from \"./platform/index" src/index.ts | head -3
```

If absent, add `import { platform } from "./platform/index.js";` near other tool imports.

- [ ] **Step 3: Apply the mapping**

For each grep hit in step 1, replace per the mapping table. Use Edit with non-replace_all (each unique context). Where a `process.platform === "darwin"` gate wraps tool registration, use `platform.systemInfo.current().platform === "darwin"`.

- [ ] **Step 4: Confirm no `os.tmpdir`/`os.homedir`/`process.platform` remain**

```bash
grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" src/index.ts | wc -l
```
Expected: `0`.

- [ ] **Step 5: Build**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

- [ ] **Step 6: Commit**

```bash
git add src/index.ts
git commit -m "refactor(platform): migrate src/index.ts to platform.* (8+ sites)"
```

---

## Task 8: Batch 2 — `tools/registry.ts` + `tools/screenshot.ts` + `tools/computer-use/driver/manager.ts`

**Files:**
- Modify: `src/tools/registry.ts`
- Modify: `src/tools/screenshot.ts`
- Modify: `src/tools/computer-use/driver/manager.ts`

- [ ] **Step 1: Audit each file**

```bash
for f in src/tools/registry.ts src/tools/screenshot.ts src/tools/computer-use/driver/manager.ts; do
  echo "--- $f ---"
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
```

- [ ] **Step 2: For each file**

Add `import { platform } from "<correct relative path>";` if not present. Relative paths:
- `src/tools/registry.ts` → `"../platform/index.js"`
- `src/tools/screenshot.ts` → `"../platform/index.js"`
- `src/tools/computer-use/driver/manager.ts` → `"../../../platform/index.js"`

Apply the mapping table.

- [ ] **Step 3: Confirm**

```bash
for f in src/tools/registry.ts src/tools/screenshot.ts src/tools/computer-use/driver/manager.ts; do
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
```
Expected: nothing matches.

- [ ] **Step 4: Build**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

- [ ] **Step 5: Commit**

```bash
git add src/tools/registry.ts src/tools/screenshot.ts src/tools/computer-use/driver/manager.ts
git commit -m "refactor(platform): migrate registry/screenshot/computer-use to platform.*"
```

---

## Task 9: Batch 3 — `tools/macos/*` (clipboard + text-to-speech)

**Files:**
- Modify: `src/tools/macos/clipboard.ts`
- Modify: `src/tools/macos/text-to-speech.ts`

- [ ] **Step 1: Audit + migrate**

```bash
for f in src/tools/macos/clipboard.ts src/tools/macos/text-to-speech.ts; do
  echo "--- $f ---"
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
```

Relative path for both: `"../../platform/index.js"`. Apply mapping table.

These files contain macOS-only tools with `process.platform === "darwin"` guards — those become `platform.systemInfo.current().platform === "darwin"`. Same semantics, ESLint-clean.

- [ ] **Step 2: Confirm + build**

```bash
for f in src/tools/macos/clipboard.ts src/tools/macos/text-to-speech.ts; do
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
npm run build 2>&1 | grep "error TS" | wc -l
```
Both expected zero/empty.

- [ ] **Step 3: Commit**

```bash
git add src/tools/macos/clipboard.ts src/tools/macos/text-to-speech.ts
git commit -m "refactor(platform): migrate macOS tools (clipboard, text-to-speech) to platform.*"
```

---

## Task 10: Batch 4 — `live-browser/frontmost.ts` + `evolution/synthesizer.ts` + `voice/adapter.ts` + `gateway/adapters/voice.ts`

**Files:**
- Modify: `src/tools/live-browser/frontmost.ts`
- Modify: `src/evolution/synthesizer.ts`
- Modify: `src/voice/adapter.ts`
- Modify: `src/gateway/adapters/voice.ts`

- [ ] **Step 1: Audit**

```bash
for f in src/tools/live-browser/frontmost.ts src/evolution/synthesizer.ts src/voice/adapter.ts src/gateway/adapters/voice.ts; do
  echo "--- $f ---"
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
```

- [ ] **Step 2: Migrate**

Relative paths:
- `src/tools/live-browser/frontmost.ts` → `"../../platform/index.js"`
- `src/evolution/synthesizer.ts` → `"../platform/index.js"`
- `src/voice/adapter.ts` → `"../platform/index.js"`
- `src/gateway/adapters/voice.ts` → `"../../platform/index.js"`

Apply mapping.

- [ ] **Step 3: Verify + build + commit**

```bash
for f in src/tools/live-browser/frontmost.ts src/evolution/synthesizer.ts src/voice/adapter.ts src/gateway/adapters/voice.ts; do
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
npm run build 2>&1 | grep "error TS" | wc -l
git add src/tools/live-browser/frontmost.ts src/evolution/synthesizer.ts src/voice/adapter.ts src/gateway/adapters/voice.ts
git commit -m "refactor(platform): migrate live-browser/voice/evolution to platform.*"
```

---

## Task 11: Batch 5 — `skills/loader.ts` + `skills/registry.ts` + `engine/runtime.ts` + `signals/collectors.ts` + `browser/puppeteer-fetcher.ts` + `swarm/node.ts` + audit remaining

**Files:**
- Modify: `src/skills/loader.ts`, `src/skills/registry.ts`, `src/engine/runtime.ts`, `src/signals/collectors.ts`, `src/browser/puppeteer-fetcher.ts`, `src/swarm/node.ts`

- [ ] **Step 1: Audit + migrate**

```bash
for f in src/skills/loader.ts src/skills/registry.ts src/engine/runtime.ts src/signals/collectors.ts src/browser/puppeteer-fetcher.ts src/swarm/node.ts; do
  echo "--- $f ---"
  grep -n "os\.tmpdir()\|os\.homedir()\|process\.platform" "$f"
done
```

Relative paths:
- `src/skills/loader.ts`, `src/skills/registry.ts` → `"../platform/index.js"`
- `src/engine/runtime.ts` → `"../platform/index.js"`
- `src/signals/collectors.ts` → `"../platform/index.js"`
- `src/browser/puppeteer-fetcher.ts` → `"../platform/index.js"`
- `src/swarm/node.ts` → `"../platform/index.js"`

Apply mapping.

- [ ] **Step 2: Final grep gate (whole repo)**

```bash
grep -rn "os\.tmpdir()\|os\.homedir()\|process\.platform" src/ --include="*.ts" | grep -v "src/platform/"
```
Expected: **zero output**. Any remaining hit means a file was missed — go back and fix.

- [ ] **Step 3: Build**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run --reporter=dot 2>&1 | tail -3
```

Expected: same pass count as before this task (i.e., the 6 Phase-A fixes plus the prior baseline; no NEW failures from migration).

- [ ] **Step 5: Commit**

```bash
git add src/skills/loader.ts src/skills/registry.ts src/engine/runtime.ts src/signals/collectors.ts src/browser/puppeteer-fetcher.ts src/swarm/node.ts
git commit -m "refactor(platform): migrate skills/engine/signals/browser/swarm to platform.* — Phase B complete"
```

---

# Phase C — ESLint warn → error

## Task 12: Bump severity + verify zero violations

**Files:**
- Modify: `eslint.config.js`

- [ ] **Step 1: Run lint before — expect zero violations**

```bash
npm run lint 2>&1 | grep "no-restricted-syntax" | wc -l
```
Expected: `0` (Phase B should have eliminated all of them).

If non-zero, Phase B missed something. Stop and finish Phase B first.

- [ ] **Step 2: Open `eslint.config.js`**

```bash
grep -n "no-restricted-syntax" eslint.config.js
```

Find the `"warn",  // TODO(cycle B0'):` line.

- [ ] **Step 3: Replace**

Use the Edit tool. Change:

```javascript
      "no-restricted-syntax": [
        "warn",  // TODO(cycle B0'): bump to "error" after migrating the 36 existing call sites
```

to:

```javascript
      "no-restricted-syntax": [
        "error",
```

- [ ] **Step 4: Run lint after — confirm clean**

```bash
npm run lint 2>&1 | tail -5
```

Expected: no errors. The build's lint step would also pass.

- [ ] **Step 5: Probe — confirm the rule fires on a synthetic violation**

Create `/tmp/eslint-probe-cycle3.ts`:

```typescript
import os from "node:os";
const t = os.tmpdir();
const p = process.platform;
console.log(t, p);
```

Run:

```bash
npx eslint /tmp/eslint-probe-cycle3.ts 2>&1 | head -10
rm /tmp/eslint-probe-cycle3.ts
```

Expected: ERRORS (not warnings) for both restricted patterns. Confirms the rule is now enforcing.

- [ ] **Step 6: Commit**

```bash
git add eslint.config.js
git commit -m "chore(eslint): bump no-restricted-syntax from warn to error — Phase C complete"
```

---

# Phase D — CI matrix workflow

## Task 13: Add `.github/workflows/test.yml` + validate

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        node: [22]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
          cache: npm

      - run: npm ci

      - name: Build
        run: npm run build

      - name: Lint
        run: npm run lint

      - name: Test
        run: npm test
        env:
          STACKOWL_DISABLE_RG: "true"
```

- [ ] **Step 2: Verify YAML syntax locally**

```bash
node -e "const yaml = require('js-yaml'); console.log(yaml.load(require('fs').readFileSync('.github/workflows/test.yml','utf-8')))" 2>&1 | head -20
```

If `js-yaml` isn't installed locally, skip — GitHub will fail the action with a clearer error.

- [ ] **Step 3: Push to a throwaway branch first**

```bash
git checkout -b cycle3-ci-validation
git add .github/workflows/test.yml
git commit -m "ci: add full-suite test matrix on ubuntu/macos/windows (validation)"
git push origin cycle3-ci-validation
```

- [ ] **Step 4: Wait for the workflow to run on the throwaway branch**

Open `https://github.com/starskrime/stackowl-personal-ai-assistant/actions` in a browser. Wait for all three OS jobs to complete on the `cycle3-ci-validation` branch.

Expected outcome:
- **ubuntu-latest:** green
- **macos-latest:** green (or fails on a real platform-specific bug — fix inline before merging)
- **windows-latest:** green (or fails on Windows-specific bugs — fix inline before merging)

If any failures appear, address them:
- Path-separator issues → use `path.join` consistently
- Symlink permissions on Windows → tests should already skip via EPERM check
- Timing-sensitive tests flaky on slow runners → bump timeouts or relax assertions

- [ ] **Step 5: Merge to main**

After all three OSes are green on the throwaway branch:

```bash
git checkout main
git merge cycle3-ci-validation
git push origin main
git branch -d cycle3-ci-validation
git push origin --delete cycle3-ci-validation
```

- [ ] **Step 6: Verify on main**

Open the Actions page again. Confirm the `test` workflow runs on the main push and all three OSes are green.

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Plan task |
|---|---|
| Phase A — fix 6 pre-existing test failures | T1–T6 |
| Phase B — migrate 35 call sites across 16 files | T7–T11 (5 batches) |
| Phase B — post-migration grep gate hits zero | T11 step 2 |
| Phase C — ESLint warn → error | T12 |
| Phase C — verify zero violations and rule fires on probe | T12 steps 1, 5 |
| Phase D — `.github/workflows/test.yml` on 3 OSes | T13 |
| Phase D — `STACKOWL_DISABLE_RG=true` for deterministic CI | T13 step 1 |
| Phase D — throwaway-branch validation before merging to main | T13 steps 3, 4 |

All sections covered.

### 2. Placeholder scan

- No "TBD" / "implement later" / "add appropriate" / "fill in details" / "Similar to Task N"
- Each Phase A task has investigative guidance but concrete decision criteria + concrete commands. Not placeholders — the open-ended part is intentional and per the spec.

### 3. Type consistency

- `platform.paths.tempdir()` signature used identically across T7–T11
- `platform.systemInfo.current().platform` used identically
- `platform` singleton is the same import everywhere (`import { platform } from "<relative>/platform/index.js"`)
- The `no-restricted-syntax` rule structure in T12 matches the existing config from Cycle 1

No drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-11-cycle-3-stability-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance + code quality), continuous execution.

**2. Inline Execution** — execute in this session via executing-plans with checkpoints.

Which approach?
