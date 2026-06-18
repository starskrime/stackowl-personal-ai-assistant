# Code Review Fixes — Specialized Owl System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all Critical, Important, and Minor issues found in the principal-engineer code review of the Specialized Owl system.

**Architecture:** Seven independent fix tasks (Critical → Important → Minor → Tests), each touching a focused file. No cross-task dependencies except Task 7 (tests) which validates the earlier fixes.

**Tech Stack:** TypeScript (strict), Node.js 22, Vitest, gray-matter, better-sqlite3.

---

## File Map

| File | What changes |
|------|-------------|
| `src/tools/credentials.ts` | Path traversal fix — use `resolve()` + allowlist |
| `src/memory/db.ts` | Remove duplicate `CREATE TABLE` block |
| `src/cli/specialization-wizard.ts` | Fix YAML frontmatter, sanitise owlName in content, stray char, welcome Enter |
| `src/gateway/core.ts` | Hoist `SecretaryRouter` to class field |
| `src/routing/secretary.ts` | Replace unsafe `!`, raise parliament threshold |
| `src/owls/specialized-parser.ts` | Throw on missing `name`, move interface to top |
| `src/owls/specialized-evolution.ts` | Move `OwlEvolutionStats` interface to top |
| `__tests__/tools/credentials.test.ts` | Add path traversal test |
| `__tests__/owls/specialized-parser.test.ts` | Add empty/malformed frontmatter test |
| `__tests__/cli/specialization-wizard.test.ts` | Fix broken assertions, add YAML structure test |

---

### Task 1: Fix path traversal in CredentialsTool [CRITICAL]

**Files:**
- Modify: `src/tools/credentials.ts:8-55`
- Test: `__tests__/tools/credentials.test.ts`

- [ ] **Step 1: Write the failing test for path traversal**

Add at end of `describe` block in `__tests__/tools/credentials.test.ts`:

```ts
  it("should reject path traversal attempt in owlName", async () => {
    const result = await CredentialsTool.execute(
      { key: "TEST_KEY", owlName: "../../etc" },
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.error).toBe("Access denied: invalid owl name");
  });

  it("should reject owlName with embedded slash", async () => {
    const result = await CredentialsTool.execute(
      { key: "TEST_KEY", owlName: "foo/../../bar" },
      { cwd: testCwd },
    );
    const parsed = JSON.parse(result);
    expect(parsed.error).toBe("Access denied: invalid owl name");
  });
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/credentials.test.ts
```

Expected: the two new tests FAIL (no protection yet) — the first returns `{ error: "Credentials file not found for ../../etc" }` not the access denied message.

- [ ] **Step 3: Fix `src/tools/credentials.ts`**

Replace the entire file with:

```ts
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const CredentialsTool: ToolImplementation = {
  definition: {
    name: "credentials_get",
    description:
      "Retrieve a credential value by key name. " +
      "Each specialized owl can only access credentials in its own folder. " +
      "Use this when you need an API key or token to perform an action.",
    parameters: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "The credential key to retrieve (e.g., ALPHA_VANTAGE_KEY)",
        },
        owlName: {
          type: "string",
          description: "The owl name whose credentials to access",
        },
      },
      required: ["key", "owlName"],
    },
  },
  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const key = args.key as string;
    const owlName = args.owlName as string;

    if (!key || !owlName) {
      return JSON.stringify({ error: "Missing key or owlName parameter" });
    }

    // Allowlist: only letters, digits, hyphens, underscores
    const safeOwlName = owlName.replace(/[^a-zA-Z0-9_-]/g, "");
    if (!safeOwlName || safeOwlName !== owlName) {
      log.tool.error(`[CredentialsTool] Path traversal attempt: ${owlName}`);
      return JSON.stringify({ error: "Access denied: invalid owl name" });
    }

    const resolvedBase = resolve(join(context.cwd, "workspace"));
    const credentialsPath = resolve(join(resolvedBase, "owls", safeOwlName, "credentials", "secrets.md"));

    if (!credentialsPath.startsWith(resolvedBase + "/")) {
      log.tool.error(`[CredentialsTool] Path traversal attempt: ${credentialsPath}`);
      return JSON.stringify({ error: "Access denied: invalid owl name" });
    }

    if (!existsSync(credentialsPath)) {
      return JSON.stringify({ error: `Credentials file not found for ${owlName}` });
    }

    try {
      const content = readFileSync(credentialsPath, "utf-8");
      const lines = content.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("#") || trimmed === "") continue;
        const [k, ...vParts] = trimmed.split("=");
        if (k.trim() === key) {
          return JSON.stringify({ key, value: vParts.join("=").trim() });
        }
      }
      return JSON.stringify({ error: `Key '${key}' not found in ${owlName} credentials` });
    } catch (error) {
      log.tool.error(`[CredentialsTool] Failed to read credentials: ${error}`);
      return JSON.stringify({ error: "Failed to read credentials" });
    }
  },
};
```

- [ ] **Step 4: Run all credential tests**

```bash
npx vitest run __tests__/tools/credentials.test.ts
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/credentials.ts __tests__/tools/credentials.test.ts
git commit -m "fix: prevent path traversal in CredentialsTool via allowlist + resolve()"
```

---

### Task 2: Remove duplicate CREATE TABLE in migration [CRITICAL]

**Files:**
- Modify: `src/memory/db.ts:930-948`

- [ ] **Step 1: Remove the duplicate block**

In `src/memory/db.ts`, find and delete the block from line 930 to 948 (the second `if (current < SCHEMA_VERSION)` block that re-creates the `owls` table). The block looks like this — remove it entirely:

```ts
    if (current < SCHEMA_VERSION) {
      // v9: user-created specialized owls (tenant-isolated)
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS owls (
          id                  TEXT PRIMARY KEY,
          owner_id            TEXT NOT NULL,
          name                TEXT NOT NULL,
          specialization      TEXT NOT NULL,
          personality_prompt TEXT NOT NULL,
          routing_rules       TEXT NOT NULL DEFAULT '[]',
          dna                 TEXT NOT NULL DEFAULT '{}',
          is_main_owl         INTEGER NOT NULL DEFAULT 0,
          created_at          TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_owls_owner ON owls(owner_id);
        CREATE INDEX IF NOT EXISTS idx_owls_name  ON owls(owner_id, name);
      `);
    }
```

After deletion, the migration section should flow: `if (current < 10) { ... }` then directly `if (current < SCHEMA_VERSION) { this.db.pragma(...) }`.

- [ ] **Step 2: Verify the file compiles**

```bash
npm run build 2>&1 | head -20
```

Expected: no TypeScript errors related to `db.ts`.

- [ ] **Step 3: Commit**

```bash
git add src/memory/db.ts
git commit -m "fix: remove duplicate CREATE TABLE owls block in schema migration"
```

---

### Task 3: Fix wizard YAML frontmatter + owlName injection [IMPORTANT]

**Files:**
- Modify: `src/cli/specialization-wizard.ts` (lines 54, 360-365, 543-616, 618-669)

This task fixes four issues in the wizard:
1. Stray Chinese character in `CHALLENGE_OPTIONS[1]`
2. Welcome step requires non-empty input but prompt says "Press Enter"
3. `_createSpecFile` generates plain markdown instead of `---` YAML frontmatter
4. `_createSpecFile` uses raw `owlName` (not sanitised `folderName`) inside YAML content
5. `generateSpecFile` also lacks `---` delimiters (same template issue)

- [ ] **Step 1: Fix stray character in CHALLENGE_OPTIONS**

In `src/cli/specialization-wizard.ts` line 54, replace:

```ts
  "medium — balanced,偶尔 challenges your assumptions",
```

with:

```ts
  "medium — balanced, occasionally challenges your assumptions",
```

- [ ] **Step 2: Fix welcome step to accept Enter (empty string)**

In `src/cli/specialization-wizard.ts` around line 360, replace:

```ts
      case "welcome":
        if (input.trim()) {
          this._step = "name";
        }
        this._showStep(ui);
        return false;
```

with:

```ts
      case "welcome":
        this._step = "name";
        this._showStep(ui);
        return false;
```

- [ ] **Step 3: Fix `_createSpecFile` to produce proper YAML frontmatter and use sanitised name**

In `src/cli/specialization-wizard.ts`, replace the `_createSpecFile` method's `content` template (lines ~560-596) and the `generateSpecFile` return value (lines ~632-668). Both use the same broken template — update both to the format with `---` delimiters and use `folderName` (not `owlName`) in the YAML values.

Replace the `content` assignment in `_createSpecFile` (the one that starts `const content = \`# ${owlName}`):

```ts
    const content = `---
name: ${folderName}
role: ${d.role}
emoji: ${d.emoji || "🦉"}
challengeLevel: ${challengeMap[d.challengeLevel ?? "medium"] ?? "medium"}
verbosity: ${verbosityMap[d.verbosity ?? "balanced"] ?? "balanced"}
tone: ${d.tone || "neutral"}
domains:
${(d.expertise ?? []).map((e) => `  - ${e}`).join("\n") || "  []"}
provider: ${d.provider === "default" ? "" : d.provider || ""}
model: ${d.model || ""}
maxTokens: ${d.maxTokens ?? ""}
allowedTools:
${(d.allowedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  []"}
deniedTools:
${(d.deniedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  []"}
capabilityConstraints:
${(d.capabilityConstraints ?? []).map((c) => `  - "${c}"`).join("\n") || "  []"}
keywords:
${(d.expertise ?? []).map((e) => `  - ${e.toLowerCase()}`).join("\n") || "  []"}
allowedSkills:
${(d.skills ?? []).map((s) => `  - ${s}`).join("\n") || "  []"}
---

# ${folderName}

${d.role}
`;
```

Replace the `generateSpecFile` return value (the one that starts with `return \`# ${d.name ?? "Unnamed"}`):

```ts
    const folderName = (d.name ?? "Unnamed").replace(/[^a-zA-Z0-9-_]/g, "");
    return `---
name: ${folderName}
role: ${d.role ?? ""}
emoji: ${d.emoji ?? "🦉"}
challengeLevel: ${challengeMap[d.challengeLevel ?? "medium"] ?? "medium"}
verbosity: ${verbosityMap[d.verbosity ?? "balanced"] ?? "balanced"}
tone: ${d.tone || "neutral"}
domains:
${(d.expertise ?? []).map((e) => `  - ${e}`).join("\n") || "  []"}
provider: ${d.provider === "default" ? "" : d.provider || ""}
model: ${d.model || ""}
maxTokens: ${d.maxTokens ?? ""}
allowedTools:
${(d.allowedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  []"}
deniedTools:
${(d.deniedTools ?? []).map((t) => `  - ${t}`).join("\n") || "  []"}
capabilityConstraints:
${(d.capabilityConstraints ?? []).map((c) => `  - "${c}"`).join("\n") || "  []"}
keywords:
${(d.expertise ?? []).map((e) => `  - ${e.toLowerCase()}`).join("\n") || "  []"}
allowedSkills:
${(d.skills ?? []).map((s) => `  - ${s}`).join("\n") || "  []"}
---

# ${folderName}

${d.role ?? ""}
`;
```

- [ ] **Step 4: Fix the broken wizard tests**

In `__tests__/cli/specialization-wizard.test.ts`:

**Fix 1** — "stay at welcome when pressing Enter" test should now PASS with empty string advancing (the test expected `getCurrentStep() === "welcome"` after empty input — now empty input advances to `name`, so update that test):

Replace:

```ts
  it("should stay at welcome when pressing Enter with no input", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("welcome");
  });
```

with:

```ts
  it("should advance from welcome to name when pressing Enter with no input", async () => {
    wizard.start(ui as unknown as TerminalRenderer);
    ui.lines = [];
    const done = await wizard.step("", ui as unknown as TerminalRenderer);
    expect(done).toBe(false);
    expect(wizard.getCurrentStep()).toBe("name");
  });
```

**Fix 2** — "advance from welcome to name when providing input" still valid, keep as is.

**Fix 3** — Fix the broken assertions in the "advance through all steps" test. The comments in the existing test acknowledge the wrong assertions. The welcome step no longer consumes input as a "name" — it just advances. So `"TradingBot"` given at `welcome` just moves to `name`, and `"Stock trading assistant"` given at `name` becomes the role. Update the assertions:

Replace the last 3 lines of that `it` block:

```ts
    expect(done).toBe(true);
    // Welcome just advances - actual owl name is entered at "name" prompt
    expect(wizard.getSpec().name).toBe("Stock trading assistant");
    expect(wizard.getSpec().emoji).toBe("3");
    expect(wizard.getSpec().challengeLevel).toBe("high");
```

with:

```ts
    expect(done).toBe(true);
    expect(wizard.getSpec().name).toBe("TradingBot");
    expect(wizard.getSpec().role).toBe("Stock trading assistant");
    expect(wizard.getSpec().emoji).toBe("📈");
    expect(wizard.getSpec().challengeLevel).toBe("high");
```

**Fix 4** — Update the "produce valid specialized_owl.md content" test to check for `---` delimiters:

Replace the assertions at the bottom of that test:

```ts
    const content = wizard.generateSpecFile();
    // Verify the spec file has the expected structure
    expect(content).toContain("name:");
    expect(content).toContain("emoji:");
    expect(content).toContain("role:");
    expect(content).toContain("challengeLevel:");
    expect(content).toContain("verbosity:");
```

with:

```ts
    const content = wizard.generateSpecFile();
    expect(content.startsWith("---\n")).toBe(true);
    expect(content).toContain("name:");
    expect(content).toContain("emoji:");
    expect(content).toContain("role:");
    expect(content).toContain("challengeLevel:");
    expect(content).toContain("verbosity:");
    // Verify gray-matter can parse the generated content
    // (imported at top of test file)
    const { data } = (await import("gray-matter")).default(content);
    expect(data.name).toBeTruthy();
    expect(data.role).toBeTruthy();
```

Also add at the top of `__tests__/cli/specialization-wizard.test.ts` (after existing imports):

```ts
import matter from "gray-matter";
```

- [ ] **Step 5: Run wizard tests**

```bash
npx vitest run __tests__/cli/specialization-wizard.test.ts
```

Expected: all tests PASS.

- [ ] **Step 6: Verify generated YAML parses with gray-matter (manual check)**

```bash
npx tsx -e "
import { SpecializationCreateWizard } from './src/cli/specialization-wizard.js';
import matter from 'gray-matter';
const w = new SpecializationCreateWizard();
w['_data'] = { name: 'TestOwl', role: 'Test', emoji: '🦉', expertise: ['testing'], skills: [] };
const content = w.generateSpecFile();
const { data } = matter(content);
console.log('name:', data.name);
console.log('role:', data.role);
if (!data.name) process.exit(1);
console.log('OK');
"
```

Expected: prints `name: TestOwl` and `OK`.

- [ ] **Step 7: Commit**

```bash
git add src/cli/specialization-wizard.ts __tests__/cli/specialization-wizard.test.ts
git commit -m "fix: wizard generates valid YAML frontmatter, sanitised name in content, welcome Enter, remove stray char"
```

---

### Task 4: Hoist SecretaryRouter to class field [IMPORTANT]

**Files:**
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Add private field declaration**

In `src/gateway/core.ts`, find the `private taskQueue: TaskQueue;` field declaration (around line 159). Add the secretary router field after it:

```ts
  private secretaryRouter: SecretaryRouter | null = null;
```

- [ ] **Step 2: Initialize in constructor or first use**

In `src/gateway/core.ts`, find the method that initializes DB-dependent singletons. Search for where `this.ctx.db` is first used in an `if (this.ctx.db)` guard. There will be a pattern like:

```ts
if (this.ctx.db) {
  // lazy init of db-dependent objects
```

If no such consolidated init block exists, add the lazy initialization directly at the top of the `if (this.ctx.db && message.userId && activeOwlName === this.ctx.owl.persona.name)` block in `handle()`:

Replace (around line 1677-1678):

```ts
    if (this.ctx.db && message.userId && activeOwlName === this.ctx.owl.persona.name) {
      const secretary = new SecretaryRouter(this.ctx.db, this.ctx.specializedRegistry);
      const routingDecision = secretary.route(text, message.userId);
```

with:

```ts
    if (this.ctx.db && message.userId && activeOwlName === this.ctx.owl.persona.name) {
      if (!this.secretaryRouter) {
        this.secretaryRouter = new SecretaryRouter(this.ctx.db, this.ctx.specializedRegistry);
      }
      const routingDecision = this.secretaryRouter.route(text, message.userId);
```

- [ ] **Step 3: Verify build**

```bash
npm run build 2>&1 | head -20
```

Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/core.ts
git commit -m "fix: hoist SecretaryRouter to class field to avoid per-message allocation"
```

---

### Task 5: Fix unsafe non-null assertion in SecretaryRouter [IMPORTANT]

**Files:**
- Modify: `src/routing/secretary.ts:128`
- Also raise parliament keyword threshold from 2 to 3 (Minor fix combined here)

- [ ] **Step 1: Fix the non-null assertion**

In `src/routing/secretary.ts`, replace line 128:

```ts
        const matchedDbOwl = dbOwls.find((o) => o.name === matchedTarget.name)!;
```

with:

```ts
        const matchedDbOwl = dbOwls.find((o) => o.name === matchedTarget.name);
        if (!matchedDbOwl) {
          log.engine.warn(`[SecretaryRouter] Matched target "${matchedTarget.name}" not found in dbOwls — falling back to direct`);
          const fallback = { type: "direct" as const, reason: "Matched owl not found in DB" };
          this.logRoutingDecision(userId, message, fallback, "failure");
          return fallback;
        }
```

- [ ] **Step 2: Raise parliament keyword threshold to 3**

In `src/routing/secretary.ts`, replace the `shouldConveneParliament` method:

```ts
  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;

    if (keywordCount >= 2) {
      return true;
    }

    if (keywordCount === 1 && message.length > 200) {
      return true;
    }

    return false;
  }
```

with:

```ts
  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;

    if (keywordCount >= 3) {
      return true;
    }

    if (keywordCount >= 2 && message.length > 200) {
      return true;
    }

    return false;
  }
```

- [ ] **Step 3: Verify build**

```bash
npm run build 2>&1 | head -20
```

Expected: no TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add src/routing/secretary.ts
git commit -m "fix: replace unsafe non-null assertion in SecretaryRouter, raise parliament threshold to 3 keywords"
```

---

### Task 6: Fix specialized-parser to throw on missing name [IMPORTANT]

**Files:**
- Modify: `src/owls/specialized-parser.ts`
- Modify: `src/owls/specialized-evolution.ts` (move interface — Minor)
- Test: `__tests__/owls/specialized-parser.test.ts`

- [ ] **Step 1: Write failing test for missing name**

Add to `__tests__/owls/specialized-parser.test.ts`:

```ts
  it("should throw when name field is missing from frontmatter", () => {
    const noName = `---
role: Some role
emoji: 🦉
---
`;
    expect(() => parseSpecializedOwl(noName)).toThrow("missing required field: name");
  });

  it("should throw when frontmatter is empty", () => {
    const empty = `---
---
`;
    expect(() => parseSpecializedOwl(empty)).toThrow("missing required field: name");
  });

  it("should throw when content has no frontmatter at all", () => {
    const noFrontmatter = `# Just a markdown heading

Some content without frontmatter.
`;
    expect(() => parseSpecializedOwl(noFrontmatter)).toThrow("missing required field: name");
  });
```

- [ ] **Step 2: Run to confirm they fail**

```bash
npx vitest run __tests__/owls/specialized-parser.test.ts
```

Expected: the 3 new tests FAIL.

- [ ] **Step 3: Fix `src/owls/specialized-parser.ts`**

Replace the entire file:

```ts
import matter from "gray-matter";
import type {
  SpecializedOwlSpec,
  SpecializedPersonality,
  SpecializedModel,
  SpecializedPermissions,
  SpecializedRoutingRules,
  SpecializedSkills,
} from "./specialized-types.js";

export function parseSpecializedOwl(content: string): SpecializedOwlSpec {
  const { data } = matter(content);

  if (!data.name || typeof data.name !== "string" || !data.name.trim()) {
    throw new Error("parseSpecializedOwl: missing required field: name");
  }

  const personality: SpecializedPersonality = {
    challengeLevel: (data.challengeLevel as SpecializedPersonality["challengeLevel"]) ?? "medium",
    verbosity: (data.verbosity as SpecializedPersonality["verbosity"]) ?? "balanced",
    tone: (data.tone as string) ?? "neutral",
  };

  const model: SpecializedModel = {
    provider: (data.provider as string) ?? "openai",
    model: (data.model as string) ?? "gpt-4",
    maxTokens: data.maxTokens as number | undefined,
  };

  const permissions: SpecializedPermissions = {
    allowedTools: Array.isArray(data.allowedTools) ? data.allowedTools : [],
    deniedTools: Array.isArray(data.deniedTools) ? data.deniedTools : [],
    capabilityConstraints: Array.isArray(data.capabilityConstraints)
      ? data.capabilityConstraints
      : [],
  };

  const routingRules: SpecializedRoutingRules = {
    keywords: Array.isArray(data.keywords) ? data.keywords : [],
  };

  const skills: SpecializedSkills = {
    allowed: Array.isArray(data.allowedSkills) ? data.allowedSkills : [],
  };

  return {
    name: data.name.trim(),
    role: (data.role as string) ?? "",
    emoji: (data.emoji as string) ?? "🦉",
    personality,
    expertise: Array.isArray(data.domains) ? data.domains : [],
    model,
    permissions,
    routingRules,
    skills,
  };
}
```

- [ ] **Step 4: Move `OwlEvolutionStats` interface to top of specialized-evolution.ts**

In `src/owls/specialized-evolution.ts`, find the `OwlEvolutionStats` interface (near the bottom) and move it to just after the imports, before the class declaration. The interface should look like:

```ts
export interface OwlEvolutionStats {
  owlName: string;
  totalConversations: number;
  avgRoutingQuality: number;
  topDomains: string[];
  lastEvolved: string | null;
}
```

Cut it from its current location and paste it before the class declaration.

- [ ] **Step 5: Run all parser tests**

```bash
npx vitest run __tests__/owls/specialized-parser.test.ts
```

Expected: all tests PASS including the 3 new ones.

- [ ] **Step 6: Verify build**

```bash
npm run build 2>&1 | head -20
```

Expected: no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add src/owls/specialized-parser.ts src/owls/specialized-evolution.ts __tests__/owls/specialized-parser.test.ts
git commit -m "fix: throw on missing name in parseSpecializedOwl, move OwlEvolutionStats interface to top"
```

---

### Task 7: Fix broken tests — secretary tenant isolation [TEST]

**Files:**
- Modify: `__tests__/owls/secretary.test.ts` (or wherever the secretary tests live)

- [ ] **Step 1: Find the test file**

```bash
find __tests__ -name "secretary*" -o -name "routing*" | head -10
```

- [ ] **Step 2: Read the broken tenant isolation test**

Open the file and find the test that creates two owls with different `ownerId` values. It currently uses `makeOwl({ name: "UserAOwl", ownerId: "user_a" })` but `makeOwl`'s type ignores `ownerId`. Confirm by reading the `makeOwl` helper.

- [ ] **Step 3: Fix the tenant isolation test**

The test helper `makeOwl` builds a `SpecializedOwl` object. If `ownerId` is not in the `Partial<>` parameter type, either:

a) Update `makeOwl` to accept `ownerId`:

```ts
function makeOwl(overrides: Partial<SpecializedOwl> = {}): SpecializedOwl {
  return {
    id: uuidv4(),
    ownerId: "user_test",
    name: "TestOwl",
    specialization: "Testing",
    personalityPrompt: "You are a test owl",
    routingRules: ["test", "testing"],
    dna: { challengeLevel: 0.5, verbosity: 0.5, expertiseDomains: ["testing"], routingQuality: 0.8, evolutionSpeed: 0.5 },
    isMainOwl: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}
```

b) Then in the tenant isolation test, create the owls with the correct `ownerId` and insert them into the DB directly via `db.owls.create()` with the proper owner, then verify `route()` only returns owls for the queried user:

```ts
it("should only route to owls owned by the requesting user", () => {
  const owlA = makeOwl({ name: "PrivateOwl", ownerId: "user_a", routingRules: ["private", "exclusive"] });
  const owlB = makeOwl({ name: "OtherOwl",  ownerId: "user_b", routingRules: ["private", "exclusive"] });
  db.owls.create(owlA);
  db.owls.create(owlB);

  const decision = router.route("private exclusive question", "user_a");
  // Should route to user_a's owl, not user_b's
  if (decision.type === "specialist") {
    expect(decision.owl.ownerId).toBe("user_a");
    expect(decision.owl.name).toBe("PrivateOwl");
  }
  // Should NOT route user_a to user_b's owl
  const decisionB = router.route("private exclusive question", "user_b");
  if (decisionB.type === "specialist") {
    expect(decisionB.owl.ownerId).toBe("user_b");
    expect(decisionB.owl.name).toBe("OtherOwl");
  }
});
```

- [ ] **Step 4: Run the secretary tests**

```bash
npx vitest run __tests__/owls/secretary.test.ts
```

(Use the actual file path found in Step 1)

Expected: all tests PASS including the fixed tenant isolation test.

- [ ] **Step 5: Commit**

```bash
git add __tests__/owls/secretary.test.ts   # use actual path
git commit -m "fix: correct tenant isolation test — makeOwl now respects ownerId override"
```

---

### Task 8: Run full test suite and verify

- [ ] **Step 1: Run all tests**

```bash
npm run test
```

Expected: all tests pass with no failures.

- [ ] **Step 2: Run lint**

```bash
npm run lint
```

Expected: no lint errors.

- [ ] **Step 3: Verify TypeScript builds clean**

```bash
npm run build
```

Expected: 0 errors, 0 warnings.

- [ ] **Step 4: Final commit (if any stray fixes needed)**

```bash
git add -p   # review any remaining changes
git commit -m "fix: final cleanup from code review pass"
```

---

## Summary of Issues Fixed

| Severity | File | Issue |
|----------|------|-------|
| Critical | `src/tools/credentials.ts` | Path traversal via `owlName` with `..` |
| Critical | `src/memory/db.ts` | Duplicate `CREATE TABLE owls` in migration |
| Important | `src/cli/specialization-wizard.ts` | No `---` YAML delimiters → all wizard owls parse as `{ name: "Unknown" }` |
| Important | `src/cli/specialization-wizard.ts` | Raw `owlName` injected into YAML content |
| Important | `src/gateway/core.ts` | `SecretaryRouter` re-allocated on every message |
| Important | `src/routing/secretary.ts:128` | Unsafe `!` non-null assertion crashes on name collision |
| Important | `src/owls/specialized-parser.ts` | Silent "Unknown" owl on empty frontmatter |
| Minor | `src/cli/specialization-wizard.ts:54` | Stray Chinese character in options |
| Minor | `src/cli/specialization-wizard.ts:360` | Welcome step rejects empty Enter |
| Minor | `src/routing/secretary.ts:199` | Parliament triggered too easily (2 keywords) |
| Minor | `src/owls/specialized-evolution.ts` | Interface declared after first use |
| Test | `__tests__/cli/specialization-wizard.test.ts` | Wrong name/emoji assertions |
| Test | `__tests__/owls/secretary.test.ts` | Tenant isolation test silently broken |
| Test | `__tests__/tools/credentials.test.ts` | Missing path traversal coverage |
| Test | `__tests__/owls/specialized-parser.test.ts` | Missing empty/malformed frontmatter coverage |
