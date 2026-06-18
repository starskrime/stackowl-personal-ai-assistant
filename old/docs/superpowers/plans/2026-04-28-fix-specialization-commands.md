# Fix Specialization Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `show`, `delete`, and `update` so they work for both DB-backed owls and folder-based owls (wizard-created), and fix the broken confirmation-parsing bug in `delete`.

**Architecture:** Extract a shared `resolveOwl` helper exported from `commands.ts` that does case-insensitive lookup across both the DB and the folder registry. Every subcommand that looks up by name uses it. Folder owl deletion removes the directory from disk; update redirects to the file path.

**Tech Stack:** TypeScript, Node.js `node:fs/promises` (rm), `node:path` (join), existing `MemoryDatabase`, `SpecializedOwlRegistry`.

---

## File Map

| File | Change |
|------|--------|
| `src/cli/commands.ts` | Add imports; add exported `resolveOwl`; fix `show`, `delete`, `update` |
| `__tests__/cli/specialization-commands.test.ts` | **New** — tests for `resolveOwl` and delete confirmation parsing |

---

## Root-Cause Summary (for context only, not to be implemented separately)

| Command | Bug |
|---------|-----|
| `show` | `db.owls.getByName` exact SQL match — folder owls never in DB → always "not found" |
| `delete` | Same lookup bug + `parts.slice(1).join(" ")` appends "yes" to the name on confirm step |
| `update` | Same lookup bug — folder owls always "not found" |
| All | `parts` is `.toLowerCase()` but `getByName` is case-sensitive SQL `name = ?` → DB owls with mixed-case names also fail |

---

## Task 1: Add `resolveOwl` helper + fix `show`

**Files:**
- Modify: `src/cli/commands.ts`
- Create: `__tests__/cli/specialization-commands.test.ts`

- [ ] **Step 1: Write the failing tests for resolveOwl**

Create `__tests__/cli/specialization-commands.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { rm, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { resolveOwl } from "../../src/cli/commands.js";

const testSpace = join(__dirname, ".test_specialization_cmds");

async function cleanWorkspace() {
  await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  await mkdir(testSpace, { recursive: true });
}

describe("resolveOwl", () => {
  let db: MemoryDatabase;
  let registry: SpecializedOwlRegistry;

  beforeEach(async () => {
    await cleanWorkspace();
    db = new MemoryDatabase(testSpace);
    registry = new SpecializedOwlRegistry();
  });

  it("finds a DB owl by exact lowercase name", () => {
    db.owls.create({
      ownerId: "local",
      name: "TradingBot",
      specialization: "Trading assistant",
      personalityPrompt: "You are a trading assistant.",
      routingRules: [],
      dna: { challengeLevel: 0.7, verbosity: 0.5, expertiseDomains: [], routingQuality: 0.5, evolutionSpeed: 0.5 },
      isMainOwl: false,
    });

    const result = resolveOwl("tradingbot", "local", db, registry);

    expect(result).not.toBeNull();
    expect(result!.source).toBe("db");
    if (result!.source === "db") {
      expect(result!.owl.name).toBe("TradingBot");
    }
  });

  it("finds a DB owl case-insensitively (mixed-case input)", () => {
    db.owls.create({
      ownerId: "local",
      name: "Calculus",
      specialization: "Math teacher",
      personalityPrompt: "You are a math teacher.",
      routingRules: [],
      dna: { challengeLevel: 0.7, verbosity: 0.5, expertiseDomains: [], routingQuality: 0.5, evolutionSpeed: 0.5 },
      isMainOwl: false,
    });

    const result = resolveOwl("CALCULUS", "local", db, registry);

    expect(result).not.toBeNull();
    expect(result!.source).toBe("db");
  });

  it("falls back to folder registry when not in DB", () => {
    // registry.get() resolves by prefix/case-insensitive already
    // We simulate it by checking that registry.get is called and result source is folder
    const fakeSpec = { name: "Calculus", role: "math teacher", emoji: "🔢", expertise: ["math"], personality: { challengeLevel: "medium" as const, verbosity: "balanced" as const, tone: "neutral" }, model: { provider: "", model: "" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [] }, skills: { allowed: [] } };
    (registry as any).specs = new Map([["calculus", fakeSpec]]);

    const result = resolveOwl("calc", "local", db, registry);

    expect(result).not.toBeNull();
    expect(result!.source).toBe("folder");
    if (result!.source === "folder") {
      expect(result!.spec.name).toBe("Calculus");
    }
  });

  it("returns null when owl not found in either source", () => {
    const result = resolveOwl("unknownowl", "local", db, registry);
    expect(result).toBeNull();
  });

  it("does not return another user's DB owl", () => {
    db.owls.create({
      ownerId: "other_user",
      name: "SecretOwl",
      specialization: "Secret",
      personalityPrompt: "",
      routingRules: [],
      dna: { challengeLevel: 0.7, verbosity: 0.5, expertiseDomains: [], routingQuality: 0.5, evolutionSpeed: 0.5 },
      isMainOwl: false,
    });

    const result = resolveOwl("secretowl", "local", db, registry);

    expect(result).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/cli/specialization-commands.test.ts
```

Expected: FAIL — `resolveOwl is not exported from commands.js`

- [ ] **Step 3: Add imports to commands.ts**

At the top of `src/cli/commands.ts`, after the existing imports, add:

```typescript
import { rm } from "node:fs/promises";
import { join } from "node:path";
import type { MemoryDatabase, SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
```

- [ ] **Step 4: Add exported `resolveOwl` helper and `ResolvedOwl` type to commands.ts**

Add this block after the `sep()` helper (before `let activeWizard`):

```typescript
export type ResolvedOwl =
  | { source: "db"; owl: SpecializedOwl }
  | { source: "folder"; spec: SpecializedOwlSpec };

export function resolveOwl(
  inputName: string,
  ownerId: string,
  db: MemoryDatabase,
  registry: SpecializedOwlRegistry | undefined,
): ResolvedOwl | null {
  const lower = inputName.toLowerCase();
  const dbOwl = db.owls.getByOwner(ownerId).find((o) => o.name.toLowerCase() === lower);
  if (dbOwl) return { source: "db", owl: dbOwl };
  const spec = registry?.get(inputName);
  if (spec) return { source: "folder", spec };
  return null;
}
```

- [ ] **Step 5: Run resolveOwl tests to verify they pass**

```bash
npx vitest run __tests__/cli/specialization-commands.test.ts
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Fix the `show` subcommand to use resolveOwl and display folder owls**

Replace the entire `if (subcmd === "show")` block (lines 104–146 in current file) with:

```typescript
  if (subcmd === "show") {
    const name = parts.slice(1).join(" ");
    if (!name) {
      ui.printInfo("Usage: /specialization show <name>");
      return true;
    }
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }

    if (result.source === "db") {
      const owl = result.owl;
      const dna = owl.dna;
      ui.printLines([
        "",
        YB(`Owl: ${owl.name}`),
        sep(),
        D("Specialization  ") + W(owl.specialization),
        D("Main Owl       ") + W(owl.isMainOwl ? "Yes" : "No"),
        D("Created        ") + W(owl.createdAt.slice(0, 10)),
        "",
        YB("DNA / Evolution"),
        sep(),
        D("Challenge Level ") + W(String(dna.challengeLevel)),
        D("Verbosity       ") + W(String(dna.verbosity)),
        D("Expertise       ") + W(dna.expertiseDomains.join(", ") || "(none)"),
        D("Routing Quality ") + W(String(dna.routingQuality)),
        D("Evolution Speed ") + W(String(dna.evolutionSpeed)),
        "",
        YB("Personality Prompt"),
        sep(),
        ...owl.personalityPrompt.split("\n").map((l: string) => D("  " + l)),
        "",
        YB("Routing Rules"),
        sep(),
        ...(owl.routingRules.length > 0
          ? owl.routingRules.map((r: string) => D("  • " + r))
          : [D("  (none)")]),
        "",
      ]);
    } else {
      const spec = result.spec;
      const folderPath = join(gateway.getWorkspacePath(), "owls", spec.name);
      ui.printLines([
        "",
        YB(`${spec.emoji || "🦉"} ${spec.name}`) + C(" [folder]"),
        sep(),
        D("Role           ") + W(spec.role),
        D("Expertise      ") + W(spec.expertise.join(", ") || "(none)"),
        D("Challenge      ") + W(spec.personality.challengeLevel),
        D("Verbosity      ") + W(spec.personality.verbosity),
        D("Tone           ") + W(spec.personality.tone),
        "",
        YB("Routing Keywords"),
        sep(),
        ...(spec.routingRules.keywords.length > 0
          ? spec.routingRules.keywords.map((k) => D("  • " + k))
          : [D("  (none)")]),
        "",
        YB("Permissions"),
        sep(),
        D("Allowed Tools  ") + W(spec.permissions.allowedTools.join(", ") || "all"),
        D("Denied Tools   ") + W(spec.permissions.deniedTools.join(", ") || "none"),
        ...(spec.permissions.capabilityConstraints.length > 0
          ? [D("Constraints    ") + W(spec.permissions.capabilityConstraints.join("; "))]
          : []),
        "",
        YB("Config File"),
        sep(),
        D("  " + folderPath + "/specialized_owl.md"),
        "",
      ]);
    }
    return true;
  }
```

- [ ] **Step 7: Build and run full test suite**

```bash
npm run build && npx vitest run __tests__/cli/specialization-commands.test.ts
```

Expected: build exits 0, all 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/cli/commands.ts
git commit -m "fix: add resolveOwl helper and fix show to work with folder owls"
```

---

## Task 2: Fix `delete` — confirmation parsing + folder deletion

**Files:**
- Modify: `src/cli/commands.ts` (the `delete` subcommand block only)

- [ ] **Step 1: Replace the entire `if (subcmd === "delete")` block**

Replace lines 155–185 (current `delete` block) with:

```typescript
  if (subcmd === "delete") {
    const lastPart = parts[parts.length - 1];
    const confirmed = (lastPart === "yes" || lastPart === "y") && parts.length > 2;
    const nameParts = confirmed ? parts.slice(1, -1) : parts.slice(1);
    const name = nameParts.join(" ");

    if (!name) {
      ui.printInfo("Usage: /specialization delete <name>");
      return true;
    }

    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }

    const displayName = result.source === "db" ? result.owl.name : result.spec.name;

    if (confirmed) {
      if (result.source === "db") {
        db.owls.delete(result.owl.id);
      } else {
        const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
        await rm(folderPath, { recursive: true, force: true });
        await gateway.reloadSpecializedRegistry();
      }
      ui.printLines(["", G(`✓ Deleted owl: ${displayName}`), ""]);
      return true;
    }

    ui.printLines([
      "",
      R(`⚠️  Delete "${displayName}"?`),
      sep(),
      D("This action cannot be undone."),
      D(""),
      D("Confirm: /specialization delete " + displayName + " yes"),
      "",
    ]);
    return true;
  }
```

- [ ] **Step 2: Build to verify no TypeScript errors**

```bash
npm run build
```

Expected: exits 0.

- [ ] **Step 3: Run full test suite to verify no regressions**

```bash
npm test 2>&1 | grep -E "✓|✗" | tail -20
```

Expected: all tests pass, no failures.

- [ ] **Step 4: Commit**

```bash
git add src/cli/commands.ts
git commit -m "fix: fix delete for folder owls — correct confirmation parsing and disk removal"
```

---

## Task 3: Fix `update` — redirect folder owls to their config file

**Files:**
- Modify: `src/cli/commands.ts` (both `update` blocks)

- [ ] **Step 1: Replace both `if (subcmd === "update")` blocks**

Replace the two update blocks (lines 187–234 in current file) with:

```typescript
  if (subcmd === "update" && parts.length > 2) {
    const name = parts[1];
    const newSpecialization = parts.slice(2).join(" ");
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }
    if (result.source === "folder") {
      const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
      ui.printLines([
        "",
        YB(`${result.spec.emoji || "🦉"} ${result.spec.name}`) + C(" [folder]"),
        sep(),
        D("Folder owls are configured via their spec file."),
        D("Edit it directly to update role, expertise, and routing rules:"),
        "",
        C("  " + folderPath + "/specialized_owl.md"),
        "",
      ]);
      return true;
    }
    if (!newSpecialization || newSpecialization.length < 5) {
      ui.printInfo("Please provide a new specialization (at least 5 characters).");
      return true;
    }
    db.owls.update(result.owl.id, { specialization: newSpecialization });
    ui.printLines([
      "",
      G(`✓ Updated owl: ${result.owl.name}`),
      sep(),
      D("New specialization: ") + W(newSpecialization),
      "",
    ]);
    return true;
  }

  if (subcmd === "update") {
    const name = parts.slice(1).join(" ");
    if (!name) {
      ui.printInfo("Usage: /specialization update <name>");
      return true;
    }
    const result = resolveOwl(name, ownerId, db, gateway.getSpecializedRegistry());
    if (!result) {
      ui.printInfo(`Owl "${name}" not found.`);
      return true;
    }
    if (result.source === "folder") {
      const folderPath = join(gateway.getWorkspacePath(), "owls", result.spec.name);
      ui.printLines([
        "",
        YB(`${result.spec.emoji || "🦉"} ${result.spec.name}`) + C(" [folder]"),
        sep(),
        D("Folder owls are configured via their spec file."),
        D("Edit it directly to update role, expertise, and routing rules:"),
        "",
        C("  " + folderPath + "/specialized_owl.md"),
        "",
      ]);
      return true;
    }
    const owl = result.owl;
    ui.printLines([
      "",
      YB(`Update Owl: ${owl.name}`),
      sep(),
      D("Specialization: ") + W(owl.specialization),
      D(""),
      D("To update specialization:"),
      D("  /specialization update " + owl.name + " <new specialization>"),
      "",
    ]);
    return true;
  }
```

- [ ] **Step 2: Build and run tests**

```bash
npm run build && npm test 2>&1 | grep -E "✓|✗" | tail -20
```

Expected: build exits 0, all tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/cli/commands.ts
git commit -m "fix: update command redirects folder owls to their spec file"
```
