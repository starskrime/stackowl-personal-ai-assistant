# Remove /helper Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the `/helper` command and its three supporting files while preserving `SpecializedOwlRegistry`, `SpecializedOwlSpec`, and all other logic that depends on them.

**Architecture:** Six callsites in telegram.ts, slack.ts, cli/commands.ts, and cli/v2/commands/registry.ts import from three files (`owl-router.ts`, `owl-creation.ts`, `handlers/helper.ts`). We delete those three files and two test files, then surgically remove every import/call that references them — leaving the registry, types, and other routing untouched.

**Tech Stack:** TypeScript, Vitest (tests), grep for callsite verification

---

## File Map

| Action | Path |
|--------|------|
| DELETE | `src/gateway/commands/owl-router.ts` |
| DELETE | `src/gateway/wizards/owl-creation.ts` |
| DELETE | `src/cli/v2/commands/handlers/helper.ts` |
| DELETE | `__tests__/gateway/commands/owl-router.test.ts` |
| DELETE | `__tests__/gateway/owl-creation-wizard.test.ts` |
| MODIFY | `src/gateway/adapters/telegram.ts` — remove bot.command("helper") block |
| MODIFY | `src/gateway/adapters/slack.ts` — remove app.command("/helper") block |
| MODIFY | `src/cli/commands.ts` — remove cmdHelper fn + COMMANDS entry + help line |
| MODIFY | `src/cli/v2/commands/registry.ts` — remove imports + /helper command block |
| KEEP   | `src/owls/specialized-registry.ts` (load-bearing) |
| KEEP   | `src/owls/specialized-types.ts` (load-bearing) |
| KEEP   | `__tests__/owls/helper-registry-compat.test.ts` (tests registry, not helper) |

---

### Task 1: Delete the three source files and two test files

**Files:**
- Delete: `src/gateway/commands/owl-router.ts`
- Delete: `src/gateway/wizards/owl-creation.ts`
- Delete: `src/cli/v2/commands/handlers/helper.ts`
- Delete: `__tests__/gateway/commands/owl-router.test.ts`
- Delete: `__tests__/gateway/owl-creation-wizard.test.ts`

- [ ] **Step 1: Verify files exist before deletion**

```bash
ls src/gateway/commands/owl-router.ts \
   src/gateway/wizards/owl-creation.ts \
   src/cli/v2/commands/handlers/helper.ts \
   __tests__/gateway/commands/owl-router.test.ts \
   __tests__/gateway/owl-creation-wizard.test.ts
```

Expected: all five paths print without error.

- [ ] **Step 2: Delete them**

```bash
rm src/gateway/commands/owl-router.ts \
   src/gateway/wizards/owl-creation.ts \
   src/cli/v2/commands/handlers/helper.ts \
   __tests__/gateway/commands/owl-router.test.ts \
   __tests__/gateway/owl-creation-wizard.test.ts
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: delete /helper source files and their tests"
```

---

### Task 2: Remove /helper from `src/gateway/adapters/telegram.ts`

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`

Find the block that starts around line 398:
```typescript
// ── /helper — channel-parity dispatcher (same router as CLI) ──────
this.bot.command("helper", async (ctx) => {
```

- [ ] **Step 1: Locate exact line numbers**

```bash
grep -n "helper\|dispatchOwlCommand\|OwlCreationWizard" src/gateway/adapters/telegram.ts
```

Expected: lines showing the `/helper` command handler and its imports.

- [ ] **Step 2: Remove the entire `/helper` command handler block**

Read the file, then delete from the `// ── /helper` comment through the closing `});` of the `this.bot.command("helper", ...)` handler. This block spans approximately 30 lines. The block begins with the comment and ends with the `});` that closes the async callback.

Do NOT touch any other `this.bot.command(...)` blocks.

- [ ] **Step 3: Run TypeScript compiler to verify no imports left dangling**

```bash
npx tsc --noEmit 2>&1 | grep telegram
```

Expected: no errors mentioning `telegram.ts`.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/adapters/telegram.ts
git commit -m "feat: remove /helper command from Telegram adapter"
```

---

### Task 3: Remove /helper from `src/gateway/adapters/slack.ts`

**Files:**
- Modify: `src/gateway/adapters/slack.ts`

- [ ] **Step 1: Locate exact line numbers**

```bash
grep -n "helper\|dispatchOwlCommand\|OwlCreationWizard" src/gateway/adapters/slack.ts
```

- [ ] **Step 2: Remove the `/helper` Slack command block**

Delete from `// ── /helper — channel-parity dispatcher` comment through the closing `});` of the `this.app.command("/helper", ...)` handler. Do NOT remove any other command handlers.

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep slack
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/adapters/slack.ts
git commit -m "feat: remove /helper command from Slack adapter"
```

---

### Task 4: Remove /helper from `src/cli/commands.ts`

**Files:**
- Modify: `src/cli/commands.ts`

There are three things to remove:
1. The `cmdHelper` async function (around lines 55–95)
2. The `"/helper"` line in the help text printout (inside `cmdHelp`)
3. The `helper:` entry in the `COMMANDS` object

- [ ] **Step 1: Locate everything to remove**

```bash
grep -n "helper\|cmdHelper\|dispatchOwlCommand\|OwlCreationWizard" src/cli/commands.ts
```

- [ ] **Step 2: Remove `cmdHelper` function**

Delete the entire `const cmdHelper: CommandFn = async (_args, _ui, _gateway) => { ... };` function. It spans from the `const cmdHelper` line through its closing `};`.

- [ ] **Step 3: Remove the help text line**

Delete the line:
```typescript
C("/helper".padEnd(20)) + D("Manage helpers"),
```

- [ ] **Step 4: Remove the COMMANDS entry**

Delete:
```typescript
helper: {
  description: "Manage helpers",
  fn: cmdHelper,
  subcommands: ["list", "show", "create", "rename", "delete", "design", "capabilities"],
},
```

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "commands.ts"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat: remove /helper command from CLI"
```

---

### Task 5: Remove /helper from `src/cli/v2/commands/registry.ts`

**Files:**
- Modify: `src/cli/v2/commands/registry.ts`

Two things to remove:
1. The import line(s) from `./handlers/helper.js`
2. The `/helper` command block in the commands array/registry

- [ ] **Step 1: Locate everything**

```bash
grep -n "helper\|handleHelper\|completeHelper\|/helper" src/cli/v2/commands/registry.ts
```

- [ ] **Step 2: Remove the import**

Delete the line:
```typescript
import {
  handleHelperList,
  handleHelperShow,
  handleHelperCreate,
  handleHelperRename,
  handleHelperDelete,
  handleHelperDesign,
  handleHelperCapabilities,
  completeHelperNames,
} from "./handlers/helper.js";
```
(or whatever exact import form exists — delete the entire import statement for `handlers/helper.js`)

- [ ] **Step 3: Remove the /helper command block**

Delete from the `{ name: "/helper", ...` entry through its closing `},` including all sub-command handler references. Based on earlier grep, this is approximately lines 249–261.

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "registry.ts"
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/cli/v2/commands/registry.ts
git commit -m "feat: remove /helper from TUI v2 command registry"
```

---

### Task 6: Full test suite and final verification

**Files:** None (verification only)

- [ ] **Step 1: Full TypeScript check**

```bash
npx tsc --noEmit 2>&1
```

Expected: 0 errors (or same pre-existing errors as before this work).

- [ ] **Step 2: Grep for any remaining dead references**

```bash
grep -rn "owl-router\|owl-creation\|handlers/helper\|dispatchOwlCommand\|OwlCreationWizard\|handleHelper\|completeHelperNames" src/ __tests__/
```

Expected: no matches.

- [ ] **Step 3: Verify registry tests still pass**

```bash
npx vitest run __tests__/owls/helper-registry-compat.test.ts
```

Expected: all tests pass (this tests `SpecializedOwlRegistry` which was NOT deleted).

- [ ] **Step 4: Run full test suite**

```bash
npm test 2>&1 | tail -20
```

Expected: same pass/fail ratio as before (the `ImprovementScheduler` quiet-hours test is a pre-existing failure unrelated to this work).

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: /helper command fully removed — registry and routing unchanged"
```
