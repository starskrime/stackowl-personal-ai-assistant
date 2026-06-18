# Cycle 3 — Stability Sweep

**Date:** 2026-05-11
**Owner:** Bakir
**Status:** Approved (sections 1-7 confirmed via brainstorming dialogue)

## Goal

Lock down the foundation before Cycle 4 (multi-agent runtime primitive). Four phases:

| Phase | Deliverable |
|---|---|
| A | Fix the 6 pre-existing test failures — every test ends up green or deleted (no `.skip()` clutter) |
| B | Migrate all 35 direct `os.tmpdir()` / `os.homedir()` / `process.platform` call sites to `platform.*` |
| C | Bump the ESLint `no-restricted-syntax` rule from `"warn"` to `"error"`, after Phase B's grep gate hits zero |
| D | Add `.github/workflows/test.yml` running the full test suite on ubuntu/macos/windows |

## Non-goals

- Multi-agent runtime work (Cycle 4)
- Code coverage reporting (defer)
- Multi-Node-version CI matrix (project only supports Node 22+)
- Renovate / Dependabot / CodeQL setup
- New features of any kind

## Architecture — phase sequencing

```
A. Fix 6 pre-existing test failures (~6 commits)
   ↓
B. Migrate 35 call sites to platform.* (~5 batched commits)
   ↓
C. ESLint warn → error (1 commit)
   ↓
D. CI matrix workflow file
```

Phase ordering rationale:
- A first because each test bug should be investigated cleanly before bulk refactors muddy the diff
- B second because it's the largest mechanical change
- C depends on B (the rule can only flip to error once zero violations remain)
- D last so CI immediately goes green rather than landing with known-red builds

## Phase A — Fix 6 pre-existing test failures

One subagent per file. Investigative, not mechanical.

### The failing tests

| File | Failing test(s) | Likely root cause |
|---|---|---|
| `__tests__/ambient.test.ts` | top-level FAIL (file won't load) | Stale import / type drift from earlier refactors; test file may reference deleted symbol |
| `__tests__/skills-installer.test.ts` | `SkillInstaller.fromLocal > copies SKILL.md from local path to target dir` | Skill sanitizer (Epic 1) may have changed the file-copy path or destination shape |
| `__tests__/pellets/pellet-retriever.test.ts` | `PelletRetriever > retrieveRelevant > should use default config values` | Config-shape drift; default config likely renamed or moved |
| `__tests__/skills/skill-usage-sqlite.test.ts` | `creates skill_usage table at v29` | Schema migration version drift — db is now past v29; test asserts schema as it was at v29 |
| `__tests__/cli/v2/commands/registry.test.ts` | `resolves /exit as alias for /quit` | Command registry lost the `/exit` alias somewhere |
| `__tests__/cli/v2/state/panel.test.ts` | `applyPanelEvent > opens a panel and sets focus to panel`, `applyPanelEvent > opening a second panel replaces the first` | Initial state shape drift — `panelStack` is undefined instead of `[]` |

### Approach per test

1. Read the test
2. Read the code under test
3. Determine if the test asserts current desired behavior OR yesterday's behavior
4. If yesterday's: update the test
5. If current: fix the bug
6. If genuinely orphaned (feature deleted): delete the test with a commit message explaining why

**No skip-marks.** Each failing test ends up green or deleted.

Each test fix → its own commit so `git blame` is clean.

## Phase B — Migrate 35 call sites to `platform.*`

### Mapping table

| Direct usage | Platform equivalent |
|---|---|
| `os.tmpdir()` | `platform.paths.tempdir()` |
| `os.homedir()` | `platform.paths.home()` |
| `process.platform === "darwin"` | `platform.systemInfo.current().platform === "darwin"` |
| `process.platform === "linux"` | `platform.systemInfo.current().platform === "linux"` |
| `process.platform === "win32"` | `platform.systemInfo.current().platform === "win32"` |
| `process.platform` (other reads) | `platform.systemInfo.current().platform` |

### Safety analysis of the macOS-tool registration gate

The Cycle 1 P4 fix wrapped macOS tool registration in `if (process.platform === "darwin")`. Migrating that line to `platform.systemInfo.current().platform === "darwin"` is correct AS LONG AS the platform layer's synchronous initial state is correct before `platform.initialize()` resolves.

`SystemInfoImpl`'s constructor (C1-T5) populates `cached.platform` synchronously from `os.platform()`. So `platform.systemInfo.current().platform` is correct even before `refresh()` runs. Only the boolean capabilities (`hasDocker`, `hasGit`, etc.) are stubbed-false until refresh — and registration code does not read those. Safe.

### File batches

- **Batch 1** — `src/index.ts` (heaviest hitter: 8+ usages, includes the macOS gate)
- **Batch 2** — `src/tools/registry.ts` + `src/tools/screenshot.ts` + `src/tools/computer-use/driver/manager.ts`
- **Batch 3** — All `src/tools/macos/*` files (clipboard, text-to-speech, etc.)
- **Batch 4** — `src/tools/live-browser/*` + `src/evolution/synthesizer.ts` + `src/voice/adapter.ts` + `src/gateway/adapters/voice.ts`
- **Batch 5** — Anything remaining (audit via `grep -rn` post-Batch-4)

### Per-batch checklist

1. Edit each file in the batch — replace direct usage with platform equivalent
2. Add `import { platform } from "<correct relative path>";` if not already present
3. Run `npm run build` — confirm zero new TypeScript errors
4. Run any tests that touch the modified files (or the full suite for confidence)
5. Commit the batch

### Post-migration grep gate

```bash
grep -rn "os\.tmpdir()\|os\.homedir()\|process\.platform" src/ --include="*.ts" | grep -v "src/platform/"
```

Expected: zero results. Phase B is done. Triggers Phase C.

## Phase C — ESLint warn → error

Single one-line change in `eslint.config.js`.

```javascript
// Before
"no-restricted-syntax": [
  "warn",  // TODO(cycle B0'): bump to "error" after migrating the 36 existing call sites
  …
]

// After
"no-restricted-syntax": [
  "error",
  …
]
```

Drop the TODO comment.

### Verification

```bash
npm run lint 2>&1 | grep "no-restricted-syntax" | wc -l
```
Expected: `0`. If non-zero, Phase B missed something — fix before flipping severity.

After this, the rule becomes a strict gate: any new direct `os.tmpdir()` / `os.homedir()` / `process.platform` outside `src/platform/` and `__tests__/` will fail CI.

One commit.

## Phase D — CI matrix workflow

### File: `.github/workflows/test.yml` (new)

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
          # Force JS fallback so search_files behavior is deterministic on
          # runners (where ripgrep may or may not be installed).
          STACKOWL_DISABLE_RG: "true"
```

### Design choices

- **`fail-fast: false`** — when one OS fails, the others still report. Mac-specific issues remain visible even if ubuntu also fails.
- **Node 22 only** — matches the project's `engines` field. No multi-version matrix; we're not a library.
- **Lint as a step** — after Phase C the lint must pass on all three OSes. Catches new ESLint violations immediately.
- **`STACKOWL_DISABLE_RG`** — pins `search_files` to the JS path on CI for deterministic output regardless of whether the runner pre-installs ripgrep. The ripgrep path is exercised on developer machines where rg is present.
- **No Docker requirement** — `code-sandbox` Docker tests auto-skip when `hasDocker: false`. Runners may or may not have Docker; tests adapt.

### Out of scope for Phase D

- Code coverage reporting (would add codecov dep; defer)
- Concurrency limits / cancel-in-progress (nice to have; doesn't block)
- Caching node_modules beyond setup-node's built-in npm cache

### Validation before merge

Push to a throwaway branch (`cycle3-ci-validation`) first. Confirm all three OSes green. Only then merge `.github/workflows/test.yml` to `main`.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Phase A test fix exposes a real bug deeper than expected | Time-box each test investigation. If it's a >30-min hole, escalate as `DONE_WITH_CONCERNS` and let the user decide whether to defer that one test. |
| Phase B `platform` import creates a circular dependency in `src/index.ts` | `src/platform/index.ts` is a leaf module — only depends on node stdlib + small deps. Safe to import from anywhere. |
| Phase B macOS-gate migration breaks darwin tool registration | Initial cached value uses real `os.platform()` synchronously in the `SystemInfoImpl` constructor — `current().platform` is correct even before `refresh()` runs. Confirmed by C1-T5 test. |
| Phase C reveals a hidden violation that the grep missed | The ESLint rule uses AST selectors (`MemberExpression`), not text matching. Strings and comments are immune by construction. |
| Phase D first run on macOS finds platform-specific failures we never saw locally | Each failure is a real bug worth fixing — that's the point of running on macOS. Address inline if small; otherwise defer to a follow-up cycle and note which tests need attention. |
| Windows runners fail on `\` vs `/` path issues | Test files we shipped this and last cycle already use `path.join` and normalize to POSIX in result paths. Likely fine. If not, fix per-test. |
| `STACKOWL_DISABLE_RG=true` in CI means ripgrep code path never sees CI scrutiny | Trade-off: deterministic test output > exercising the rg path. Ripgrep tests on dev machines where rg is installed. A future enhancement could add a dedicated `rg-enabled` job. |
| Removed test (Phase A) covered behavior that turns out to matter later | Each deletion includes a commit message explaining *why*. `git log -- __tests__/<file>` reconstructs the rationale. |

## Deliverables

1. ~6 commits — Phase A test fixes (one per file)
2. ~5 commits — Phase B call-site migration (one per batch)
3. 1 commit — Phase C ESLint bump
4. 1 commit — Phase D `.github/workflows/test.yml` (after throwaway-branch validation)
5. `npm run lint` passes locally and in CI
6. `npm test` passes locally and in CI on all three OSes
7. `grep -rn "os\.tmpdir()\|os\.homedir()\|process\.platform" src/ | grep -v "src/platform/"` returns zero
