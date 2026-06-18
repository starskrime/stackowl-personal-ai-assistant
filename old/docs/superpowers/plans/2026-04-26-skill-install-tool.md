# Skill Install Tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `install_skill` tool to the OwlEngine so users can ask the assistant to install skills from ClawHub, GitHub, or local paths, with immediate hot-reload into the running session.

**Architecture:** New file `src/tools/skill-install.ts` exports `SkillInstallTool`. It wraps the existing `SkillInstaller` + `ClawHubClient` and calls `SkillsRegistry.loadFromDirectory()` for hot-reload. One registration line added to `src/index.ts`.

**Tech Stack:** TypeScript, Vitest, existing `SkillInstaller`, `ClawHubClient`, `parseInstallSource`, `SkillsRegistry`

---

## File Structure

| File | Change |
|---|---|
| `src/tools/skill-install.ts` | Create — `SkillInstallTool` class |
| `src/index.ts` | Modify — add import + registration |
| `__tests__/skill-install.test.ts` | Create — unit tests |

---

### Task 1: Create `SkillInstallTool`

**Files:**
- Create: `src/tools/skill-install.ts`
- Test: `__tests__/skill-install.test.ts`

Context: Tools in this codebase implement the `ToolImplementation` interface from `src/tools/registry.ts`:
```typescript
interface ToolImplementation {
  definition: ToolDefinition;      // sent to LLM
  category?: ToolCategory;
  source?: string;
  execute(args: Record<string, unknown>, context: ToolContext): Promise<string>;
}
```
`ToolContext` is `{ cwd: string; engineContext?: EngineContext }`.
`EngineContext.skillsRegistry` is `SkillsRegistry | undefined`.
`SkillsRegistry.loadFromDirectory(dirPath)` re-scans `dirPath` for `*/SKILL.md` subdirectories and registers found skills.

- [ ] **Step 1: Write the failing tests**

Create `__tests__/skill-install.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { SkillInstallTool } from "../src/tools/skill-install.js";

describe("SkillInstallTool", () => {
  it("has the correct tool name", () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    expect(tool.definition.name).toBe("install_skill");
  });

  it("has a source parameter in its schema", () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const props = tool.definition.parameters?.properties as Record<string, unknown>;
    expect(props).toHaveProperty("source");
  });

  it("execute() returns a string (never throws) on a non-existent local path", async () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const result = await tool.execute(
      { source: "./non-existent-skill-path-xyz" },
      { cwd: "/tmp" },
    );
    expect(typeof result).toBe("string");
    expect(result.length).toBeGreaterThan(0);
  });

  it("execute() returns error string on bad GitHub slug without network", async () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const result = await tool.execute(
      { source: "github:bad-user/bad-repo/bad/path" },
      { cwd: "/tmp" },
    );
    expect(typeof result).toBe("string");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/skill-install.test.ts
```

Expected: FAIL — `SkillInstallTool` not found.

- [ ] **Step 3: Create `src/tools/skill-install.ts`**

```typescript
import { join } from "node:path";
import { SkillInstaller, parseInstallSource } from "../skills/installer.js";
import { ClawHubClient } from "../skills/clawhub.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";

export class SkillInstallTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "install_skill",
    description:
      "Install a skill from ClawHub, GitHub, or a local path, then activate it immediately in this session. " +
      "Sources: bare slug `user/skill-name` or `clawhub:user/skill-name` (ClawHub); " +
      "`github:user/repo/path/to/skill` or `github:user/repo/path@branch` (GitHub); " +
      "`./relative/path` or `/absolute/path` (local). " +
      "After a successful install the skill is ready to use — no restart needed.",
    parameters: {
      type: "object",
      properties: {
        source: {
          type: "string",
          description:
            "Install source. Examples: `ivangdavila/self-improving`, " +
            "`github:some-user/skills-repo/my-skill`, `./workspace/skills/my-skill`",
        },
      },
      required: ["source"],
    },
  };

  category = "files" as const;
  source = "builtin";

  constructor(private readonly workspacePath: string) {}

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const sourceArg = (args.source as string | undefined)?.trim();
    if (!sourceArg) return "Error: `source` is required.";

    const parsed = parseInstallSource(sourceArg);
    const skillsDir = join(this.workspacePath, "skills");

    try {
      if (parsed.type === "github") {
        const installer = new SkillInstaller(this.workspacePath);
        await installer.fromGitHub(parsed.rawUrl, parsed.skillName);
      } else if (parsed.type === "local") {
        const installer = new SkillInstaller(this.workspacePath);
        await installer.fromLocal(parsed.localPath);
      } else {
        const client = new ClawHubClient();
        await client.install(parsed.slug, skillsDir);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return `Failed to install "${sourceArg}": ${msg}`;
    }

    // Hot-reload the registry so the skill is active immediately
    const registry = context.engineContext?.skillsRegistry;
    if (registry) {
      await registry.loadFromDirectory(skillsDir);
      return `✓ Installed "${parsed.skillName}" and loaded it into this session. The skill is now active.`;
    }

    return `✓ Installed "${parsed.skillName}". Restart the assistant to activate it.`;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/skill-install.test.ts
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tools/skill-install.ts __tests__/skill-install.test.ts
git commit -m "feat(tools): add install_skill tool with hot-reload"
```

---

### Task 2: Register in `src/index.ts`

**Files:**
- Modify: `src/index.ts` — add import near line 158, add registration near line 265

Context: Tools are collected in a `toolRegistry.registerAll([...])` call around line 265. `workspacePath` is a `string` already in scope at that point (defined at line 208 as `resolve(basePath, config.workspace)`).

- [ ] **Step 1: Add the import**

In `src/index.ts`, find the block of tool imports (around line 158 where `RecallMemoryTool` and `RememberTool` are imported). Add after the last tool import in that block:

```typescript
import { SkillInstallTool } from "./tools/skill-install.js";
```

- [ ] **Step 2: Add to registerAll**

In `src/index.ts`, find the `toolRegistry.registerAll([` block (around line 265). Add after `new CronTool(workspacePath),` (around line 293):

```typescript
    new SkillInstallTool(workspacePath),
```

- [ ] **Step 3: Run the build to confirm no TypeScript errors**

```bash
npm run build
```

Expected: exits 0, no errors.

- [ ] **Step 4: Run the full test suite**

```bash
npx vitest run
```

Expected: all previously passing tests still pass; `skill-install.test.ts` 4/4 pass.

- [ ] **Step 5: Commit**

```bash
git add src/index.ts
git commit -m "feat(tools): register SkillInstallTool in engine"
```

---

## Self-Review

**Spec coverage:**
- Tool definition with `source` param → Task 1 ✅
- Routes to GitHub/local/ClawHub installer → Task 1 ✅
- Hot-reload via `skillsRegistry.loadFromDirectory` → Task 1 ✅
- Fallback message when registry absent → Task 1 ✅
- Registration in `src/index.ts` → Task 2 ✅
- No changes to `ClawHubClient` or `SkillInstaller` → confirmed, none made ✅

**Type consistency:** `ToolImplementation`, `ToolContext`, `ToolDefinition` — all imported from their canonical paths. `category = "files" as const` matches `ToolCategory` union. `parsed.skillName` available on all three `InstallSource` shapes.

**No placeholders:** All steps have complete code.
