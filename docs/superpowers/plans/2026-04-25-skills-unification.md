# Skills Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge `src/instincts/` into `src/skills/` under a single unified SKILL.md format so behavioral skills are installable from ClawHub, GitHub URLs, and local paths — exactly like task skills.

**Architecture:** Extend the existing `Skill` type with optional `conditions`/`trigger`/`relevantOwls`/`priority` fields. The presence of `conditions` routes a skill to the reactive engine. Move `InstinctEngine` to `src/skills/engine.ts` accepting `Skill[]` instead of `Instinct[]`. Delete `src/instincts/` entirely.

**Tech Stack:** TypeScript (NodeNext), Vitest, gray-matter (frontmatter), Node.js `fs/promises`

**Spec:** `docs/superpowers/specs/2026-04-25-skills-unification-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/skills/types.ts` | Modify | Add optional behavioral fields to `Skill` interface |
| `src/skills/parser.ts` | Modify | Parse `trigger`, `conditions`, `relevant_owls`, `priority` from frontmatter |
| `src/skills/registry.ts` | Modify | Add `getBehavioral(owlName: string): Skill[]` method |
| `src/skills/engine.ts` | Create | Move `InstinctEngine` here; change `Instinct[]` → `Skill[]` |
| `src/skills/migrator.ts` | Create | One-time rename of `workspace/instincts/*.INSTINCT.md` → `workspace/skills/` |
| `src/skills/installer.ts` | Create | GitHub URL + local path install (ClawHub already exists in clawhub.ts) |
| `src/skills/defaults/cost_alarm/SKILL.md` | Create | Migrated from `src/instincts/defaults/cost-alarm/INSTINCT.md` |
| `src/instincts/` | Delete | After all imports are re-pointed |
| `src/gateway/types.ts` | Modify | Replace `InstinctRegistry`/`InstinctEngine` fields with `skillsEngine` |
| `src/gateway/core.ts` | Modify | Use `skillsRegistry.getBehavioral()` + `skillsEngine.evaluate()` |
| `src/index.ts` | Modify | Wire migrator on startup; replace instinct bootstrap with skillsEngine |
| `src/cli/components/left-panel.ts` | Modify | Rename "Instincts" label to "Skills" |
| `__tests__/skills.test.ts` | Modify | Add tests for behavioral field parsing and `getBehavioral()` |
| `__tests__/skills-engine.test.ts` | Create | Tests for the moved engine |
| `__tests__/skills-migrator.test.ts` | Create | Tests for the migrator |
| `__tests__/skills-installer.test.ts` | Create | Tests for GitHub + local installer |

---

## Task 1: Extend Skill type with behavioral fields

**Files:**
- Modify: `src/skills/types.ts`

- [ ] **Step 1: Add behavioral fields to the `Skill` interface**

Open `src/skills/types.ts`. After the `composition?: SkillComposition;` line (around line 124), add:

```typescript
  /** Behavioral fields — present only when skill is reactive */
  trigger?: "context" | "schedule" | "event";
  conditions?: string[];
  relevantOwls?: string[];
  priority?: "low" | "medium" | "high" | "critical";
```

The full `Skill` interface section after the change (lines 102–130 area):

```typescript
export interface Skill {
  name: string;
  description: string;
  instructions: string;
  metadata: SkillMetadata;
  sourcePath: string;
  enabled: boolean;
  config?: Record<string, unknown>;
  requiredEnv?: string[];
  requiredBins?: string[];
  usage?: SkillUsageStats;
  composition?: SkillComposition;
  parameters?: Record<string, SkillParameter>;
  steps?: SkillStep[];
  /** Behavioral fields — present only when skill is reactive */
  trigger?: "context" | "schedule" | "event";
  conditions?: string[];
  relevantOwls?: string[];
  priority?: "low" | "medium" | "high" | "critical";
}
```

- [ ] **Step 2: Verify TypeScript compiles cleanly**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/skills/types.ts
git commit -m "feat(skills): add optional behavioral fields to Skill interface"
```

---

## Task 2: Extend SkillParser to read behavioral frontmatter

**Files:**
- Modify: `src/skills/parser.ts`
- Modify: `__tests__/skills.test.ts`

- [ ] **Step 1: Write failing tests for behavioral field parsing**

Open `__tests__/skills.test.ts`. Find the `SkillParser` describe block and add a new nested describe after the existing tests:

```typescript
describe("behavioral field parsing", () => {
  it("parses conditions, trigger, relevant_owls, priority from frontmatter", () => {
    const parser = new SkillParser();
    const raw = `---
name: cost_alarm
description: Warn about cost implications
trigger: context
conditions:
  - "user mentions cloud costs"
  - "user compares managed vs self-hosted"
relevant_owls:
  - "scrooge"
  - "*"
priority: high
---
Act on your cost-alarm instinct.
`;
    const skill = parser.parseContent(raw, "/tmp/cost_alarm/SKILL.md");
    expect(skill.trigger).toBe("context");
    expect(skill.conditions).toEqual([
      "user mentions cloud costs",
      "user compares managed vs self-hosted",
    ]);
    expect(skill.relevantOwls).toEqual(["scrooge", "*"]);
    expect(skill.priority).toBe("high");
  });

  it("leaves behavioral fields undefined when absent", () => {
    const parser = new SkillParser();
    const raw = `---
name: git_commit
description: Create a git commit
---
Stage and commit changes.
`;
    const skill = parser.parseContent(raw, "/tmp/git_commit/SKILL.md");
    expect(skill.trigger).toBeUndefined();
    expect(skill.conditions).toBeUndefined();
    expect(skill.relevantOwls).toBeUndefined();
    expect(skill.priority).toBeUndefined();
  });

  it("defaults trigger to 'context' when conditions present but trigger absent", () => {
    const parser = new SkillParser();
    const raw = `---
name: cost_alarm
description: Cost warning
conditions:
  - "user mentions billing"
---
Warn about costs.
`;
    const skill = parser.parseContent(raw, "/tmp/cost_alarm/SKILL.md");
    expect(skill.trigger).toBe("context");
    expect(skill.conditions).toEqual(["user mentions billing"]);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills.test.ts
```

Expected: 3 failures in "behavioral field parsing" describe block.

- [ ] **Step 3: Implement behavioral field parsing in SkillParser.parseContent()**

In `src/skills/parser.ts`, update the `parseContent` method's return statement to include behavioral fields. After the existing `...(steps.length > 0 ? { steps } : {}),` line, add:

```typescript
    const behavioralFields = this.parseBehavioralFields(data);

    return {
      name: data.name,
      description: data.description,
      instructions: content.trim(),
      metadata,
      sourcePath,
      enabled: true,
      requiredEnv,
      requiredBins,
      ...(Object.keys(parameters).length > 0 ? { parameters } : {}),
      ...(steps.length > 0 ? { steps } : {}),
      ...behavioralFields,
    };
```

Then add the `parseBehavioralFields` private method to the `SkillParser` class, after `parseSteps`:

```typescript
  private parseBehavioralFields(
    data: Record<string, unknown>,
  ): Partial<Pick<Skill, "trigger" | "conditions" | "relevantOwls" | "priority">> {
    const conditions = Array.isArray(data.conditions)
      ? (data.conditions as string[]).filter((c) => typeof c === "string")
      : undefined;

    if (!conditions || conditions.length === 0) {
      return {};
    }

    const trigger =
      typeof data.trigger === "string"
        ? (data.trigger as "context" | "schedule" | "event")
        : "context";

    const relevantOwls = Array.isArray(data.relevant_owls)
      ? (data.relevant_owls as string[]).filter((o) => typeof o === "string")
      : ["*"];

    const priority =
      typeof data.priority === "string" &&
      ["low", "medium", "high", "critical"].includes(data.priority)
        ? (data.priority as "low" | "medium" | "high" | "critical")
        : "medium";

    return { trigger, conditions, relevantOwls, priority };
  }
```

Also add `Skill` to the import from `./types.js` at the top of `parser.ts`:

```typescript
import type {
  Skill,
  SkillMetadata,
  SkillParameter,
  SkillStep,
} from "./types.js";
```

(It is already imported — no change needed if `Skill` is already there. If not, add it.)

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills.test.ts
```

Expected: all tests pass including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/skills/parser.ts __tests__/skills.test.ts
git commit -m "feat(skills): parse behavioral fields (conditions, trigger, relevantOwls, priority)"
```

---

## Task 3: Add getBehavioral() to SkillsRegistry

**Files:**
- Modify: `src/skills/registry.ts`
- Modify: `__tests__/skills.test.ts`

- [ ] **Step 1: Write failing test for getBehavioral()**

In `__tests__/skills.test.ts`, find the `SkillsRegistry` describe block and add:

```typescript
describe("getBehavioral", () => {
  it("returns only skills with conditions, filtered by owlName", () => {
    const registry = new SkillsRegistry();

    const taskSkill = makeSkill({ name: "git_commit" });

    const behavioralAll = makeSkill({
      name: "cost_alarm",
      conditions: ["user mentions billing"],
      relevantOwls: ["*"],
      trigger: "context" as const,
      priority: "high" as const,
    });

    const behavioralScrooge = makeSkill({
      name: "budget_strict",
      conditions: ["user wants to overspend"],
      relevantOwls: ["scrooge"],
      trigger: "context" as const,
      priority: "medium" as const,
    });

    const behavioralOther = makeSkill({
      name: "other_instinct",
      conditions: ["some condition"],
      relevantOwls: ["other_owl"],
      trigger: "context" as const,
      priority: "low" as const,
    });

    registry.register(taskSkill);
    registry.register(behavioralAll);
    registry.register(behavioralScrooge);
    registry.register(behavioralOther);

    const result = registry.getBehavioral("scrooge");
    const names = result.map((s) => s.name);

    expect(names).toContain("cost_alarm");      // relevantOwls: ["*"]
    expect(names).toContain("budget_strict");   // relevantOwls: ["scrooge"]
    expect(names).not.toContain("git_commit");  // no conditions
    expect(names).not.toContain("other_instinct"); // wrong owl
  });

  it("returns empty array when no behavioral skills registered", () => {
    const registry = new SkillsRegistry();
    registry.register(makeSkill({ name: "plain" }));
    expect(registry.getBehavioral("any_owl")).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills.test.ts
```

Expected: 2 failures — `getBehavioral is not a function`.

- [ ] **Step 3: Implement getBehavioral() in SkillsRegistry**

In `src/skills/registry.ts`, add this method after `listEnabled()`:

```typescript
  /**
   * Get reactive (behavioral) skills for a specific owl.
   * Returns skills where conditions.length > 0 and relevantOwls includes owlName or "*".
   */
  getBehavioral(owlName: string): Skill[] {
    const name = owlName.toLowerCase();
    return this.listEnabled().filter((skill) => {
      if (!skill.conditions || skill.conditions.length === 0) return false;
      const owls = skill.relevantOwls ?? ["*"];
      return owls.some((o) => o === "*" || o.toLowerCase() === name);
    });
  }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills.test.ts
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/skills/registry.ts __tests__/skills.test.ts
git commit -m "feat(skills): add getBehavioral() to SkillsRegistry"
```

---

## Task 4: Create src/skills/engine.ts (move InstinctEngine)

**Files:**
- Create: `src/skills/engine.ts`
- Create: `__tests__/skills-engine.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/skills-engine.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { SkillsEngine } from "../src/skills/engine.js";
import type { Skill } from "../src/skills/types.js";
import type { ModelProvider } from "../src/providers/base.js";

vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

function makeProvider(responseContent: string): ModelProvider {
  return {
    chat: vi.fn().mockResolvedValue({ content: responseContent }),
  } as unknown as ModelProvider;
}

function makeBehavioralSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    name: "cost_alarm",
    description: "Warn about costs",
    instructions: "Act on cost alarm.",
    metadata: { name: "cost_alarm", description: "Warn about costs" },
    sourcePath: "/tmp/cost_alarm/SKILL.md",
    enabled: true,
    trigger: "context",
    conditions: ["user mentions billing"],
    relevantOwls: ["*"],
    priority: "high",
    ...overrides,
  };
}

describe("SkillsEngine", () => {
  it("returns null when no skills provided", async () => {
    const engine = new SkillsEngine();
    const provider = makeProvider('{"triggered": false, "skillId": null}');
    const result = await engine.evaluate("hello world", [], {
      provider,
      owl: { persona: { name: "test" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
    expect(provider.chat).not.toHaveBeenCalled();
  });

  it("returns the triggered skill when LLM says triggered=true", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '{"triggered": true, "skillId": "cost_alarm"}',
    );
    const result = await engine.evaluate("how much does this cloud setup cost?", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).not.toBeNull();
    expect(result?.name).toBe("cost_alarm");
  });

  it("returns null when LLM says triggered=false", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '{"triggered": false, "skillId": null}',
    );
    const result = await engine.evaluate("tell me a joke", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
  });

  it("returns null and does not throw on malformed LLM JSON", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider("not json at all");
    const result = await engine.evaluate("billing question", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result).toBeNull();
  });

  it("strips markdown code fences from LLM response", async () => {
    const engine = new SkillsEngine();
    const skill = makeBehavioralSkill();
    const provider = makeProvider(
      '```json\n{"triggered": true, "skillId": "cost_alarm"}\n```',
    );
    const result = await engine.evaluate("cloud costs question", [skill], {
      provider,
      owl: { persona: { name: "scrooge" } } as any,
      config: {} as any,
    });
    expect(result?.name).toBe("cost_alarm");
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills-engine.test.ts
```

Expected: fail — `SkillsEngine` not found.

- [ ] **Step 3: Create src/skills/engine.ts**

```typescript
/**
 * StackOwl — Skills Engine
 *
 * Evaluates whether the current user message triggers any reactive (behavioral) skill.
 * All skills are evaluated in ONE LLM call (batch classification).
 */

import type { Skill } from "./types.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { log } from "../logger.js";

export class SkillsEngine {
  async evaluate(
    userMessage: string,
    availableSkills: Skill[],
    context: {
      provider: ModelProvider;
      owl: OwlInstance;
      config: StackOwlConfig;
    },
  ): Promise<Skill | null> {
    if (availableSkills.length === 0) return null;

    const { provider } = context;

    const skillList = availableSkills
      .map(
        (skill, idx) =>
          `${idx + 1}. ID: "${skill.name}"\n   Conditions:\n${(skill.conditions ?? []).map((c) => `   - ${c}`).join("\n")}`,
      )
      .join("\n\n");

    const systemPrompt =
      `You are a classifier that decides whether a user message triggers a behavioral skill.\n` +
      `You will be given a list of skills (each with conditions) and a user message.\n` +
      `Return a JSON object: { "triggered": true|false, "skillId": "<name>" | null }\n` +
      `Only trigger a skill if its conditions are CLEARLY met by the message.\n` +
      `If multiple skills match, return only the first (highest priority) one.\n` +
      `Output ONLY valid JSON — no prose, no code fences.`;

    const userPrompt =
      `SKILLS:\n${skillList}\n\n` +
      `USER MESSAGE:\n"${userMessage}"\n\n` +
      `Which skill (if any) is triggered? Return JSON.`;

    try {
      const response = await provider.chat(
        [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        undefined,
        { temperature: 0, maxTokens: 128 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```(?:json)?/, "")
          .replace(/```$/, "")
          .trim();
      }

      const parsed = JSON.parse(jsonStr) as {
        triggered: boolean;
        skillId: string | null;
      };

      if (parsed.triggered && parsed.skillId) {
        const triggered = availableSkills.find(
          (s) => s.name === parsed.skillId,
        );
        if (triggered) {
          log.engine.info(`[Skills] ⚡ Triggered: "${triggered.name}"`);
          return triggered;
        }
      }

      return null;
    } catch (err) {
      log.engine.warn(
        `[Skills] Batch evaluation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills-engine.test.ts
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/skills/engine.ts __tests__/skills-engine.test.ts
git commit -m "feat(skills): add SkillsEngine (replaces InstinctEngine, accepts Skill[])"
```

---

## Task 5: Create src/skills/migrator.ts

**Files:**
- Create: `src/skills/migrator.ts`
- Create: `__tests__/skills-migrator.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/skills-migrator.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillsMigrator } from "../src/skills/migrator.js";
import * as fsp from "node:fs/promises";
import * as fs from "node:fs";

vi.mock("node:fs/promises");
vi.mock("node:fs");
vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

describe("SkillsMigrator", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("copies INSTINCT.md to SKILL.md and logs the migration", async () => {
    const existsSyncMock = vi.mocked(fs.existsSync);
    const readdirMock = vi.mocked(fsp.readdir);
    const mkdirMock = vi.mocked(fsp.mkdir);
    const copyFileMock = vi.mocked(fsp.copyFile);

    // instincts dir exists, skills dir exists
    existsSyncMock.mockImplementation((p) => {
      const path = String(p);
      return path.includes("instincts") || path.includes("skills");
    });

    readdirMock.mockResolvedValue([
      { name: "cost-alarm", isDirectory: () => true, isFile: () => false } as any,
    ]);

    // INSTINCT.md exists for cost-alarm
    const origExistsSync = existsSyncMock.getMockImplementation();
    existsSyncMock.mockImplementation((p) => {
      const path = String(p);
      if (path.includes("INSTINCT.md")) return true;
      return origExistsSync ? origExistsSync(p) : false;
    });

    mkdirMock.mockResolvedValue(undefined);
    copyFileMock.mockResolvedValue(undefined);

    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();

    expect(count).toBe(1);
    expect(copyFileMock).toHaveBeenCalledOnce();
    const [src, dest] = copyFileMock.mock.calls[0];
    expect(String(src)).toContain("INSTINCT.md");
    expect(String(dest)).toContain("SKILL.md");
  });

  it("returns 0 when instincts directory does not exist", async () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);
    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();
    expect(count).toBe(0);
  });

  it("skips subdirectories that have no INSTINCT.md", async () => {
    vi.mocked(fs.existsSync).mockImplementation((p) => {
      return !String(p).includes("INSTINCT.md");
    });
    vi.mocked(fsp.readdir).mockResolvedValue([
      { name: "empty-dir", isDirectory: () => true, isFile: () => false } as any,
    ]);
    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();
    expect(count).toBe(0);
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills-migrator.test.ts
```

Expected: fail — `SkillsMigrator` not found.

- [ ] **Step 3: Create src/skills/migrator.ts**

```typescript
/**
 * StackOwl — Skills Migrator
 *
 * One-time migration: renames INSTINCT.md files in workspace/instincts/
 * to SKILL.md files in workspace/skills/.
 * Non-destructive — originals are left in place.
 */

import { readdir, mkdir, copyFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export class SkillsMigrator {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  async migrate(): Promise<number> {
    const instinctsDir = join(this.workspacePath, "instincts");
    if (!existsSync(instinctsDir)) return 0;

    const skillsDir = join(this.workspacePath, "skills");

    let entries: string[];
    try {
      const dirEntries = await readdir(instinctsDir, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return 0;
    }

    let migrated = 0;
    for (const entry of entries) {
      const instinctPath = join(instinctsDir, entry, "INSTINCT.md");
      if (!existsSync(instinctPath)) continue;

      const skillName = entry.replace(/-/g, "_");
      const destDir = join(skillsDir, skillName);
      const destPath = join(destDir, "SKILL.md");

      try {
        await mkdir(destDir, { recursive: true });
        await copyFile(instinctPath, destPath);
        log.engine.info(
          `[Migrator] instincts/${entry}/INSTINCT.md → skills/${skillName}/SKILL.md`,
        );
        migrated++;
      } catch (err) {
        log.engine.warn(
          `[Migrator] Failed to migrate ${entry}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return migrated;
  }
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills-migrator.test.ts
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/skills/migrator.ts __tests__/skills-migrator.test.ts
git commit -m "feat(skills): add SkillsMigrator for one-time INSTINCT.md → SKILL.md migration"
```

---

## Task 6: Create src/skills/installer.ts (GitHub + local sources)

**Files:**
- Create: `src/skills/installer.ts`
- Create: `__tests__/skills-installer.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/skills-installer.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillInstaller, parseInstallSource } from "../src/skills/installer.js";
import * as fsp from "node:fs/promises";
import * as fs from "node:fs";

vi.mock("node:fs/promises");
vi.mock("node:fs");
vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

describe("parseInstallSource", () => {
  it("parses github: prefix", () => {
    const result = parseInstallSource("github:anthropics/superpowers/skills/tdd");
    expect(result.type).toBe("github");
    expect(result.rawUrl).toBe(
      "https://raw.githubusercontent.com/anthropics/superpowers/main/skills/tdd/SKILL.md",
    );
    expect(result.skillName).toBe("tdd");
  });

  it("parses github: with branch pin", () => {
    const result = parseInstallSource("github:user/repo/path/to/skill@dev");
    expect(result.type).toBe("github");
    expect(result.rawUrl).toBe(
      "https://raw.githubusercontent.com/user/repo/dev/path/to/skill/SKILL.md",
    );
    expect(result.skillName).toBe("skill");
  });

  it("parses local ./ prefix", () => {
    const result = parseInstallSource("./my-skills/cost_alarm");
    expect(result.type).toBe("local");
    expect(result.localPath).toContain("cost_alarm");
    expect(result.skillName).toBe("cost_alarm");
  });

  it("parses local absolute path", () => {
    const result = parseInstallSource("/home/user/skills/my_skill");
    expect(result.type).toBe("local");
    expect(result.skillName).toBe("my_skill");
  });

  it("returns clawhub type for plain slugs", () => {
    const result = parseInstallSource("git_commit");
    expect(result.type).toBe("clawhub");
    expect(result.slug).toBe("git_commit");
  });

  it("returns clawhub type for clawhub: prefix", () => {
    const result = parseInstallSource("clawhub:git_commit");
    expect(result.type).toBe("clawhub");
    expect(result.slug).toBe("git_commit");
  });
});

describe("SkillInstaller.fromLocal", () => {
  beforeEach(() => vi.resetAllMocks());

  it("copies SKILL.md from local path to target dir", async () => {
    vi.mocked(fs.existsSync).mockImplementation((p) => {
      return String(p).includes("SKILL.md") || String(p).includes("cost_alarm");
    });
    vi.mocked(fsp.mkdir).mockResolvedValue(undefined);
    vi.mocked(fsp.copyFile).mockResolvedValue(undefined);

    const installer = new SkillInstaller("/workspace");
    await installer.fromLocal("./fixtures/cost_alarm");

    expect(fsp.copyFile).toHaveBeenCalledOnce();
  });

  it("throws when SKILL.md not found at local path", async () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);
    const installer = new SkillInstaller("/workspace");
    await expect(installer.fromLocal("./nonexistent")).rejects.toThrow(
      "SKILL.md not found",
    );
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills-installer.test.ts
```

Expected: fail — `parseInstallSource` and `SkillInstaller` not found.

- [ ] **Step 3: Create src/skills/installer.ts**

```typescript
/**
 * StackOwl — Skill Installer
 *
 * Installs skills from GitHub URLs and local paths.
 * ClawHub installs are handled by ClawHubClient (clawhub.ts).
 */

import { mkdir, copyFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename, resolve } from "node:path";
import { log } from "../logger.js";

export type InstallSource =
  | { type: "github"; rawUrl: string; skillName: string }
  | { type: "local"; localPath: string; skillName: string }
  | { type: "clawhub"; slug: string; skillName: string };

/**
 * Parse an install argument into a typed source descriptor.
 *
 * Supported formats:
 *   github:user/repo/path/to/skill
 *   github:user/repo/path/to/skill@branch
 *   ./relative/path/to/skill
 *   /absolute/path/to/skill
 *   clawhub:slug
 *   slug   (defaults to clawhub)
 */
export function parseInstallSource(input: string): InstallSource {
  if (input.startsWith("github:")) {
    const rest = input.slice("github:".length);
    const [pathPart, branch = "main"] = rest.split("@");
    const segments = pathPart.split("/");
    const user = segments[0];
    const repo = segments[1];
    const skillPath = segments.slice(2).join("/");
    const skillName = basename(skillPath);
    const rawUrl = `https://raw.githubusercontent.com/${user}/${repo}/${branch}/${skillPath}/SKILL.md`;
    return { type: "github", rawUrl, skillName };
  }

  if (input.startsWith("./") || input.startsWith("/")) {
    const localPath = resolve(input);
    const skillName = basename(localPath);
    return { type: "local", localPath, skillName };
  }

  if (input.startsWith("clawhub:")) {
    const slug = input.slice("clawhub:".length);
    return { type: "clawhub", slug, skillName: slug };
  }

  return { type: "clawhub", slug: input, skillName: input };
}

export class SkillInstaller {
  private workspacePath: string;

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
  }

  /**
   * Install a skill from a GitHub raw URL.
   */
  async fromGitHub(rawUrl: string, skillName: string): Promise<void> {
    log.engine.info(`[Installer] Downloading ${rawUrl}...`);

    const response = await fetch(rawUrl);
    if (!response.ok) {
      throw new Error(
        `GitHub fetch failed: ${response.status} ${response.statusText} — ${rawUrl}`,
      );
    }

    const content = await response.text();
    const destDir = join(this.workspacePath, "skills", skillName);
    const destPath = join(destDir, "SKILL.md");

    await mkdir(destDir, { recursive: true });
    await writeFile(destPath, content, "utf-8");
    log.engine.info(`[Installer] Installed ${skillName} to ${destPath}`);
  }

  /**
   * Install a skill from a local directory path.
   */
  async fromLocal(sourcePath: string): Promise<void> {
    const resolved = resolve(sourcePath);
    const skillName = basename(resolved);
    const srcFile = join(resolved, "SKILL.md");

    if (!existsSync(srcFile)) {
      throw new Error(`SKILL.md not found at ${srcFile}`);
    }

    const destDir = join(this.workspacePath, "skills", skillName);
    const destFile = join(destDir, "SKILL.md");

    await mkdir(destDir, { recursive: true });
    await copyFile(srcFile, destFile);
    log.engine.info(`[Installer] Copied ${skillName} from ${resolved} to ${destDir}`);
  }
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills-installer.test.ts
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/skills/installer.ts __tests__/skills-installer.test.ts
git commit -m "feat(skills): add SkillInstaller for GitHub URL and local path install sources"
```

---

## Task 7: Migrate built-in INSTINCT.md to SKILL.md

**Files:**
- Create: `src/skills/defaults/cost_alarm/SKILL.md`
- Delete: `src/instincts/defaults/cost-alarm/INSTINCT.md` (after copying)

- [ ] **Step 1: Create the migrated SKILL.md**

Create `src/skills/defaults/cost_alarm/SKILL.md` with this content:

```markdown
---
name: "cost_alarm"
trigger: "context"
conditions:
  - "user mentions cloud costs or billing"
  - "user is comparing managed services vs self-hosted"
  - "user is provisioning tracking infrastructure"
  - "user talks about moving from one provider to another"
relevant_owls: ["scrooge"]
priority: "high"
description: "Warn about cloud cost implications and provide cost estimates when triggered"
---

[SKILL TRIGGERED: COST ALARM]
When responding to the user's latest message, you MUST immediately act on your cost-alarm skill:

1. Identify the most expensive component of the user's proposed plan or question.
2. Provide a rough back-of-the-napkin estimate of the monthly cost. Show the math.
3. Suggest a cheaper alternative or warn them if the cost is likely to exceed $100/mo.
4. Do NOT hand-wave the numbers. Be specific, even if making reasonable assumptions.
```

- [ ] **Step 2: Verify the parser loads the new file**

```bash
node -e "
import('./src/skills/parser.js').then(async ({ SkillParser }) => {
  const p = new SkillParser();
  const s = await p.parse('./src/skills/defaults/cost_alarm/SKILL.md');
  console.log('name:', s.name);
  console.log('conditions:', s.conditions);
  console.log('trigger:', s.trigger);
  console.log('priority:', s.priority);
});
"
```

Expected output:
```
name: cost_alarm
conditions: [ 'user mentions cloud costs or billing', ... ]
trigger: context
priority: high
```

- [ ] **Step 3: Commit the new file**

```bash
git add src/skills/defaults/cost_alarm/SKILL.md
git commit -m "feat(skills): migrate cost-alarm instinct to unified SKILL.md format"
```

---

## Task 8: Update gateway/types.ts

**Files:**
- Modify: `src/gateway/types.ts`

- [ ] **Step 1: Replace instinct imports with skills imports**

In `src/gateway/types.ts`, find lines 143–144 and 207–208:

```typescript
import type { InstinctRegistry } from "../instincts/registry.js";
import type { InstinctEngine } from "../instincts/engine.js";
```

Replace with:

```typescript
import type { SkillsEngine } from "../skills/engine.js";
```

Then find lines 207–208:

```typescript
  instinctRegistry?: InstinctRegistry;
  instinctEngine?: InstinctEngine;
```

Replace with:

```typescript
  skillsEngine?: SkillsEngine;
```

(The `skillsRegistry` field already exists in the context — `instinctRegistry` is redundant after the merge.)

- [ ] **Step 2: Verify TypeScript compiles cleanly**

```bash
npx tsc --noEmit
```

Expected: errors only in `gateway/core.ts` and `index.ts` which still reference `instinctRegistry`/`instinctEngine`. That is expected — they will be fixed in Tasks 9 and 10.

- [ ] **Step 3: Commit**

```bash
git add src/gateway/types.ts
git commit -m "refactor(gateway): replace InstinctRegistry/InstinctEngine context fields with SkillsEngine"
```

---

## Task 9: Update gateway/core.ts

**Files:**
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Update the instinct evaluation block**

In `src/gateway/core.ts`, find lines 1008–1027 (the instinct evaluation block):

```typescript
    // Evaluate instincts — may inject behavioral constraints
    let text = message.text;
    if (this.ctx.instinctEngine && this.ctx.instinctRegistry) {
      const instincts = this.ctx.instinctRegistry.getContextInstincts(
        this.ctx.owl.persona.name,
      );
      const triggered = await this.ctx.instinctEngine.evaluate(
        text,
        instincts,
        {
          provider: this.ctx.provider,
          owl: this.ctx.owl,
          config: this.ctx.config,
        },
      );
      if (triggered) {
        log.engine.info(`Instinct triggered: ${triggered.name}`);
        text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - INSTINCT TRIGGERED]\n${triggered.actionPrompt}`;
      }
    }
```

Replace with:

```typescript
    // Evaluate behavioral skills — may inject reactive constraints
    let text = message.text;
    if (this.ctx.skillsEngine && this.ctx.skillsRegistry) {
      const behavioralSkills = this.ctx.skillsRegistry.getBehavioral(
        this.ctx.owl.persona.name,
      );
      const triggered = await this.ctx.skillsEngine.evaluate(
        text,
        behavioralSkills,
        {
          provider: this.ctx.provider,
          owl: this.ctx.owl,
          config: this.ctx.config,
        },
      );
      if (triggered) {
        log.engine.info(`Skill triggered: ${triggered.name}`);
        text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - SKILL TRIGGERED]\n${triggered.instructions}`;
      }
    }
```

- [ ] **Step 2: Verify TypeScript compiles (gateway/core.ts only)**

```bash
npx tsc --noEmit 2>&1 | grep "gateway/core"
```

Expected: no errors from `gateway/core.ts`.

- [ ] **Step 3: Commit**

```bash
git add src/gateway/core.ts
git commit -m "refactor(gateway): use skillsEngine + skillsRegistry.getBehavioral() for reactive skills"
```

---

## Task 10: Update src/index.ts

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Replace instinct imports with skills imports**

In `src/index.ts`, find lines 134–135:

```typescript
import { InstinctRegistry } from "./instincts/registry.js";
import { InstinctEngine } from "./instincts/engine.js";
```

Replace with:

```typescript
import { SkillsEngine } from "./skills/engine.js";
import { SkillsMigrator } from "./skills/migrator.js";
import { SkillInstaller, parseInstallSource } from "./skills/installer.js";
```

- [ ] **Step 2: Replace instinct bootstrap in the bootstrap() function**

Find lines 550–552:

```typescript
  const instinctRegistry = new InstinctRegistry(workspacePath);
  await instinctRegistry.loadAll();
  const instinctEngine = new InstinctEngine();
```

Replace with:

```typescript
  const migrator = new SkillsMigrator(workspacePath);
  const migratedCount = await migrator.migrate();
  if (migratedCount > 0) {
    console.log(chalk.dim(`  [Migrated ${migratedCount} instinct(s) to skills]`));
  }
  const skillsEngine = new SkillsEngine();
```

- [ ] **Step 3: Update the bootstrap context object**

Find lines 698–699:

```typescript
    instinctRegistry,
    instinctEngine,
```

Replace with:

```typescript
    skillsEngine,
```

Find lines 966–967:

```typescript
      instinctRegistry: b.instinctRegistry,
      instinctEngine: b.instinctEngine,
```

Replace with:

```typescript
      skillsEngine: b.skillsEngine,
```

- [ ] **Step 4: Update the CLI install command to support GitHub + local**

Find the `if (opts.install)` block (around line 1499). Replace the entire block:

```typescript
  if (opts.install) {
    const source = parseInstallSource(opts.install);
    const targetDir = config.skills?.directories?.[0] ?? "./workspace/skills";

    const workspaceRoot = resolve(config.skills?.directories?.[0] ?? "./workspace/skills", "../..");
    if (source.type === "github") {
      const installer = new SkillInstaller(workspaceRoot);
      console.log(chalk.cyan(`Installing "${source.skillName}" from GitHub...`));
      try {
        await installer.fromGitHub(source.rawUrl, source.skillName);
        console.log(chalk.green(`✓ Installed ${source.skillName}`));
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(chalk.red(`GitHub install failed: ${msg}`));
      }
    } else if (source.type === "local") {
      const installer = new SkillInstaller(workspaceRoot);
      console.log(chalk.cyan(`Installing "${source.skillName}" from local path...`));
      try {
        await installer.fromLocal(source.localPath);
        console.log(chalk.green(`✓ Installed ${source.skillName}`));
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(chalk.red(`Local install failed: ${msg}`));
      }
    } else {
      // clawhub
      const clawHub = new ClawHubClient();
      console.log(chalk.cyan(`Installing "${source.slug}" from ClawHub...`));
      try {
        await clawHub.install(source.slug, targetDir);
        console.log(chalk.green(`\n✓ Successfully installed!`));
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(chalk.red(`Installation failed: ${msg}`));
      }
    }
    return;
  }
```

- [ ] **Step 5: Verify TypeScript compiles cleanly**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/index.ts
git commit -m "refactor(index): wire SkillsEngine, SkillsMigrator, and unified installer in bootstrap"
```

---

## Task 11: Rename CLI label and delete src/instincts/

**Files:**
- Modify: `src/cli/components/left-panel.ts`
- Delete: `src/instincts/` directory

- [ ] **Step 1: Rename "Instincts" label in left-panel.ts**

In `src/cli/components/left-panel.ts`, find line 68:

```typescript
    add("  " + PURPLE("◆") + " " + LBL("Instincts") + "   " + (props.instincts > 0 ? AMBER.bold(props.instincts + " triggered") : MUT("—")));
```

Replace with:

```typescript
    add("  " + PURPLE("◆") + " " + LBL("Skills") + "      " + (props.instincts > 0 ? AMBER.bold(props.instincts + " triggered") : MUT("—")));
```

- [ ] **Step 2: Verify no remaining imports from src/instincts/**

```bash
grep -r "from.*instincts" src/ --include="*.ts"
```

Expected: no output.

- [ ] **Step 3: Delete src/instincts/**

```bash
rm -rf src/instincts/
```

- [ ] **Step 4: Run the full test suite**

```bash
npm run test
```

Expected: all tests pass.

- [ ] **Step 5: Compile to confirm no broken imports**

```bash
npm run build
```

Expected: successful build with no errors.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "feat(skills): complete skills unification — delete src/instincts/, rename CLI label"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `npx vitest run __tests__/skills.test.ts` — passes (behavioral parsing + getBehavioral)
- [ ] `npx vitest run __tests__/skills-engine.test.ts` — passes
- [ ] `npx vitest run __tests__/skills-migrator.test.ts` — passes
- [ ] `npx vitest run __tests__/skills-installer.test.ts` — passes
- [ ] `npm run test` — full suite passes
- [ ] `npm run build` — clean TypeScript compile
- [ ] `stackowl skills install github:user/repo/path` — installs SKILL.md from GitHub
- [ ] `stackowl skills install ./local/skill` — copies SKILL.md to workspace/skills/
- [ ] `stackowl skills install clawhub:git_commit` — ClawHub install still works
- [ ] `src/instincts/` directory no longer exists
- [ ] A SKILL.md with `conditions` field triggers reactive behavior at runtime
