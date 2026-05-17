# BMAD Parliament Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dynamically load BMAD agents from the installed `bmad-method` npm package at runtime — no hardcoded TypeScript constants — and register them as `SpecializedOwlSpec` entries so they auto-appear in routing, @mentions, and Parliament.

**Architecture:** A single new file `src/owls/bmad-agent-loader.ts` is the sole integration point. It resolves the `bmad-method` package root via `createRequire`, scans `src/bmm-skills/*/customize.toml`, filters for TOML files that contain `[agent]` with both `name` and `title` keys, then converts each one to a `SpecializedOwlSpec`. Gateway core calls the loader once at startup and pushes specs into the existing `SpecializedOwlRegistry` via a new `registerSpec()` method. The Parliament tool is updated to also pull from `specializedRegistry` when the owlRegistry has fewer than 2 owls. When `bmad-method` is upgraded and adds new agents, they automatically appear — zero maintenance.

**Tech Stack:** Node.js `createRequire`, `@iarna/toml` (already in node_modules as a transitive dep — no install needed), `SpecializedOwlRegistry`, `SpecializedOwlSpec`, TypeScript

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| CREATE | `src/owls/bmad-agent-loader.ts` | Sole BMAD integration point: scan → parse → convert → return specs |
| MODIFY | `src/owls/specialized-types.ts` | Add `source?` and `bmadSkillName?` optional fields |
| MODIFY | `src/owls/specialized-registry.ts` | Add `registerSpec(spec): void` method |
| MODIFY | `src/gateway/core.ts` | Call BmadAgentLoader at startup; push specs into registry |
| MODIFY | `src/tools/parliament.ts` | Fall back to specializedRegistry when owlRegistry is thin |
| CREATE | `__tests__/owls/bmad-agent-loader.test.ts` | Unit tests for the loader |

---

### Task 1: Extend `SpecializedOwlSpec` with source metadata

**Files:**
- Modify: `src/owls/specialized-types.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/owls/bmad-agent-loader.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import type { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

describe("SpecializedOwlSpec source fields", () => {
  it("accepts source and bmadSkillName optional fields", () => {
    const spec: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Business Analyst",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: ["business analysis", "requirements"],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: ["business", "requirements", "stakeholder"] },
      skills: { allowed: [] },
      additionalPrompt: "You are Mary.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    expect(spec.source).toBe("bmad");
    expect(spec.bmadSkillName).toBe("bmad-agent-analyst");
  });

  it("source and bmadSkillName are optional (undefined by default)", () => {
    const spec: SpecializedOwlSpec = {
      name: "Custom",
      type: "specialist",
      role: "Custom role",
      emoji: "🦉",
      personality: { challengeLevel: "low", verbosity: "concise", tone: "casual" },
      expertise: [],
      model: { provider: "ollama", model: "llama3" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [] },
      skills: { allowed: [] },
      additionalPrompt: "",
    };
    expect(spec.source).toBeUndefined();
    expect(spec.bmadSkillName).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts
```

Expected: FAIL — `source` and `bmadSkillName` don't exist on `SpecializedOwlSpec`.

- [ ] **Step 3: Add fields to `src/owls/specialized-types.ts`**

Open the file and add after `additionalPrompt: string;` (before `folderPath?`):

```typescript
  /** Origin of this spec — "bmad" for npm-loaded agents, "custom" for user-created, "builtin" for shipped owls */
  source?: "bmad" | "custom" | "builtin";
  /** The bmad-method skill directory name, e.g. "bmad-agent-analyst" */
  bmadSkillName?: string;
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts
```

Expected: PASS

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "specialized-types"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/owls/specialized-types.ts __tests__/owls/bmad-agent-loader.test.ts
git commit -m "feat(types): add source and bmadSkillName to SpecializedOwlSpec"
```

---

### Task 2: Add `registerSpec()` to `SpecializedOwlRegistry`

**Files:**
- Modify: `src/owls/specialized-registry.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/owls/bmad-agent-loader.test.ts`:

```typescript
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";

describe("SpecializedOwlRegistry.registerSpec", () => {
  it("registers a spec that is then retrievable by name", () => {
    const registry = new SpecializedOwlRegistry();
    const spec: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Business Analyst",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: ["business analysis"],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: ["business"] },
      skills: { allowed: [] },
      additionalPrompt: "You are Mary.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    registry.registerSpec(spec);
    const retrieved = registry.get("Mary");
    expect(retrieved).toBeDefined();
    expect(retrieved!.name).toBe("Mary");
    expect(retrieved!.source).toBe("bmad");
  });

  it("registerSpec overwrites a spec with the same name", () => {
    const registry = new SpecializedOwlRegistry();
    const spec1: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Role v1",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: [],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [] },
      skills: { allowed: [] },
      additionalPrompt: "",
    };
    const spec2 = { ...spec1, role: "Role v2" };
    registry.registerSpec(spec1);
    registry.registerSpec(spec2);
    expect(registry.get("Mary")!.role).toBe("Role v2");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts 2>&1 | tail -15
```

Expected: FAIL — `registerSpec is not a function`.

- [ ] **Step 3: Add `registerSpec` to `SpecializedOwlRegistry`**

Open `src/owls/specialized-registry.ts`. After the closing brace of `saveDNA()`, add:

```typescript
  registerSpec(spec: SpecializedOwlSpec): void {
    log.engine.debug("[SpecializedOwlRegistry] registerSpec", { name: spec.name, source: spec.source });
    this.specs.set(spec.name.toLowerCase(), spec);
  }
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts
```

Expected: all tests PASS

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "specialized-registry"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/owls/specialized-registry.ts __tests__/owls/bmad-agent-loader.test.ts
git commit -m "feat(registry): add registerSpec() method for dynamic spec injection"
```

---

### Task 3: Create `BmadAgentLoader`

**Files:**
- Create: `src/owls/bmad-agent-loader.ts`
- Modify: `__tests__/owls/bmad-agent-loader.test.ts`

This is the sole integration point. It:
1. Resolves `bmad-method` package root via `createRequire`
2. Scans `src/bmm-skills/*/customize.toml` with `fs.globSync` (Node 22) or manual `readdir`
3. Parses each TOML file with `@iarna/toml`
4. Filters: only files where `parsed.agent?.name` and `parsed.agent?.title` are both strings
5. Converts to `SpecializedOwlSpec` using BMAD fields

**TOML → SpecializedOwlSpec mapping:**

| TOML field | SpecializedOwlSpec field |
|------------|--------------------------|
| `agent.name` | `name` |
| `agent.title` | `role` |
| `agent.icon` | `emoji` |
| `agent.identity` + `agent.communication_style` + `agent.principles` joined | `additionalPrompt` |
| `agent.role` | included in `additionalPrompt` |
| `agent.principles` | used to derive routing keywords |
| skill dir basename | `bmadSkillName` |
| always | `source: "bmad"` |
| always | `type: "specialist"` |

For `expertise`: split `agent.title` words + extract nouns from `agent.role` (simple: split on spaces, filter length>3).
For `routingRules.keywords`: use `agent.title` words + any `agent.principles` word-tokens over length 4.
For `personality`: `challengeLevel: "medium"`, `verbosity: "balanced"`, `tone: agent.communication_style ?? "professional"` (truncated to 50 chars).
For `model`: `{ provider: "anthropic", model: "claude-sonnet-4-6" }` (inherits global config override at runtime).
For `permissions`: empty arrays.

- [ ] **Step 1: Write tests for BmadAgentLoader**

Append to `__tests__/owls/bmad-agent-loader.test.ts`:

```typescript
import { BmadAgentLoader } from "../../src/owls/bmad-agent-loader.js";

describe("BmadAgentLoader", () => {
  it("loadAll returns at least 6 BMAD agents (bmad-method is installed)", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    expect(specs.length).toBeGreaterThanOrEqual(6);
  });

  it("all returned specs have required SpecializedOwlSpec fields", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    for (const spec of specs) {
      expect(typeof spec.name).toBe("string");
      expect(spec.name.length).toBeGreaterThan(0);
      expect(typeof spec.role).toBe("string");
      expect(typeof spec.emoji).toBe("string");
      expect(spec.source).toBe("bmad");
      expect(typeof spec.bmadSkillName).toBe("string");
      expect(spec.type).toBe("specialist");
      expect(Array.isArray(spec.expertise)).toBe(true);
      expect(Array.isArray(spec.routingRules.keywords)).toBe(true);
    }
  });

  it("Mary (Business Analyst) is loaded from bmad-agent-analyst", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    const mary = specs.find((s) => s.name === "Mary");
    expect(mary).toBeDefined();
    expect(mary!.emoji).toBe("📊");
    expect(mary!.bmadSkillName).toBe("bmad-agent-analyst");
    expect(mary!.source).toBe("bmad");
  });

  it("loadAll returns empty array when bmad-method is not installed", async () => {
    const loader = new BmadAgentLoader({ packageName: "bmad-method-nonexistent-xyz" });
    const specs = await loader.loadAll();
    expect(specs).toEqual([]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts 2>&1 | tail -20
```

Expected: FAIL — `BmadAgentLoader` does not exist yet.

- [ ] **Step 3: Implement `src/owls/bmad-agent-loader.ts`**

```typescript
/**
 * StackOwl — BMAD Agent Loader
 *
 * Dynamically loads BMAD agents from the installed bmad-method npm package.
 * Scans src/bmm-skills/*\/customize.toml, filters for agent entries, and
 * converts them to SpecializedOwlSpec objects.
 *
 * NO hardcoded agent names or fields. When bmad-method upgrades and adds
 * new agents, they appear automatically on next startup.
 */

import { createRequire } from "node:module";
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, basename } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import { log } from "../logger.js";

interface BmadTomlAgent {
  name?: string;
  title?: string;
  icon?: string;
  role?: string;
  identity?: string;
  communication_style?: string;
  principles?: string[];
}

interface BmadToml {
  agent?: BmadTomlAgent;
}

export interface BmadAgentLoaderOptions {
  packageName?: string;
}

export class BmadAgentLoader {
  private packageName: string;

  constructor(options: BmadAgentLoaderOptions = {}) {
    this.packageName = options.packageName ?? "bmad-method";
    log.engine.debug("BmadAgentLoader: init", { packageName: this.packageName });
  }

  async loadAll(): Promise<SpecializedOwlSpec[]> {
    log.engine.debug("BmadAgentLoader.loadAll: entry", { packageName: this.packageName });

    const bmadRoot = this.resolveBmadRoot();
    if (!bmadRoot) {
      log.engine.info("BmadAgentLoader.loadAll: package not found, skipping", { packageName: this.packageName });
      return [];
    }

    const skillsDir = join(bmadRoot, "src", "bmm-skills");
    if (!existsSync(skillsDir)) {
      log.engine.warn("BmadAgentLoader.loadAll: bmm-skills dir missing", { skillsDir });
      return [];
    }

    const tomlPaths = await this.findAgentTomls(skillsDir);
    log.engine.debug("BmadAgentLoader.loadAll: found TOML candidates", { count: tomlPaths.length });

    const specs: SpecializedOwlSpec[] = [];
    for (const tomlPath of tomlPaths) {
      try {
        const raw = await readFile(tomlPath, "utf-8");
        const parsed = this.parseToml(raw);
        if (!this.isAgentToml(parsed)) continue;
        const spec = this.toSpec(parsed.agent!, basename(basename(tomlPath, ".toml"), "customize"));
        // bmadSkillName = parent directory name
        const skillName = basename(join(tomlPath, "..", ".."));
        spec.bmadSkillName = skillName;
        specs.push(spec);
        log.engine.info("BmadAgentLoader.loadAll: loaded agent", { name: spec.name, skill: skillName });
      } catch (err) {
        log.engine.warn("BmadAgentLoader.loadAll: failed to parse", { tomlPath, err: String(err) });
      }
    }

    log.engine.debug("BmadAgentLoader.loadAll: exit", { loaded: specs.length });
    return specs;
  }

  private resolveBmadRoot(): string | null {
    try {
      const req = createRequire(import.meta.url);
      const pkgPath = req.resolve(`${this.packageName}/package.json`);
      const root = join(pkgPath, "..");
      log.engine.debug("BmadAgentLoader.resolveBmadRoot: resolved", { root });
      return root;
    } catch {
      return null;
    }
  }

  private async findAgentTomls(skillsDir: string): Promise<string[]> {
    const paths: string[] = [];
    let categoryDirs: string[];
    try {
      const entries = await readdir(skillsDir, { withFileTypes: true });
      categoryDirs = entries.filter((e) => e.isDirectory()).map((e) => join(skillsDir, e.name));
    } catch {
      return [];
    }

    for (const catDir of categoryDirs) {
      let skillDirs: string[];
      try {
        const entries = await readdir(catDir, { withFileTypes: true });
        skillDirs = entries.filter((e) => e.isDirectory()).map((e) => join(catDir, e.name));
      } catch {
        continue;
      }
      for (const skillDir of skillDirs) {
        const tomlPath = join(skillDir, "customize.toml");
        if (existsSync(tomlPath)) paths.push(tomlPath);
      }
    }
    return paths;
  }

  private parseToml(raw: string): BmadToml {
    // @iarna/toml is a CJS module — use createRequire to load it in ESM context
    const req = createRequire(import.meta.url);
    const toml = req("@iarna/toml") as { parse: (s: string) => unknown };
    return toml.parse(raw) as BmadToml;
  }

  private isAgentToml(parsed: BmadToml): boolean {
    return (
      typeof parsed.agent?.name === "string" &&
      parsed.agent.name.length > 0 &&
      typeof parsed.agent?.title === "string" &&
      parsed.agent.title.length > 0
    );
  }

  private toSpec(agent: BmadTomlAgent, _skillDir: string): SpecializedOwlSpec {
    const name = agent.name!;
    const title = agent.title!;
    const icon = agent.icon ?? "🦉";
    const role = agent.role ?? title;
    const identity = agent.identity ?? "";
    const commStyle = agent.communication_style ?? "professional";
    const principles: string[] = Array.isArray(agent.principles) ? agent.principles : [];

    const additionalPromptParts = [
      identity ? `Identity: ${identity}` : "",
      commStyle ? `Communication style: ${commStyle}` : "",
      principles.length > 0 ? `Principles:\n${principles.map((p) => `- ${p}`).join("\n")}` : "",
    ].filter(Boolean);

    const expertise = this.extractExpertise(title, role);
    const keywords = this.extractKeywords(title, principles);

    return {
      name,
      type: "specialist",
      role,
      emoji: icon,
      personality: {
        challengeLevel: "medium",
        verbosity: "balanced",
        tone: commStyle.slice(0, 50),
      },
      expertise,
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords },
      skills: { allowed: [] },
      additionalPrompt: additionalPromptParts.join("\n\n"),
      source: "bmad",
    };
  }

  private extractExpertise(title: string, role: string): string[] {
    const words = `${title} ${role}`.split(/\s+/).filter((w) => w.length > 3);
    return [...new Set(words.map((w) => w.toLowerCase()))].slice(0, 8);
  }

  private extractKeywords(title: string, principles: string[]): string[] {
    const titleWords = title.split(/\s+/).filter((w) => w.length > 2).map((w) => w.toLowerCase());
    const principleWords = principles
      .flatMap((p) => p.split(/\s+/))
      .filter((w) => w.length > 4)
      .map((w) => w.toLowerCase().replace(/[^a-z]/g, ""))
      .filter(Boolean);
    return [...new Set([...titleWords, ...principleWords])].slice(0, 15);
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts
```

Expected: all tests PASS

- [ ] **Step 5: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "bmad-agent-loader"
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/owls/bmad-agent-loader.ts __tests__/owls/bmad-agent-loader.test.ts
git commit -m "feat(owls): BmadAgentLoader — dynamic BMAD agent loading from npm package"
```

---

### Task 4: Wire BmadAgentLoader into gateway startup

**Files:**
- Modify: `src/gateway/core.ts`

The gateway core already initializes `SpecializedOwlRegistry` (around line 773–791). After `loadAll()` completes, call the BMAD loader and push each spec via `registerSpec()`.

- [ ] **Step 1: Locate the registry initialization block**

```bash
grep -n "specializedRegistry\|loadAll\|SpecializedOwlRegistry" src/gateway/core.ts | head -20
```

- [ ] **Step 2: Add BMAD loader call**

Find the block that calls `ctx.specializedRegistry.loadAll(workspacePath).then(async () => { ... })`.

Add the BMAD loader import at the top of the file (after existing imports):
```typescript
import { BmadAgentLoader } from "../owls/bmad-agent-loader.js";
```

Inside the `.then()` callback, after the existing `log.engine.info` line that logs how many owls were loaded, add:

```typescript
      // Load BMAD agents dynamically from the installed npm package
      try {
        const bmadLoader = new BmadAgentLoader();
        const bmadSpecs = await bmadLoader.loadAll();
        for (const spec of bmadSpecs) {
          ctx.specializedRegistry!.registerSpec(spec);
        }
        log.engine.info(`[registry] BmadAgentLoader registered ${bmadSpecs.length} BMAD agents`);
      } catch (err) {
        log.engine.warn("[registry] BmadAgentLoader failed (non-fatal)", { err: String(err) });
      }
```

- [ ] **Step 3: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "core.ts"
```

Expected: no errors.

- [ ] **Step 4: Smoke test — start the app and check logs**

```bash
# Start with a quick timeout to see startup logs
timeout 8 npm run dev 2>&1 | grep -i "bmad\|registry\|BmadAgent" || true
```

Expected: lines like `[registry] BmadAgentLoader registered 6 BMAD agents`.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat(gateway): wire BmadAgentLoader into startup registry population"
```

---

### Task 5: Update Parliament tool to use BMAD agents as fallback participants

**Files:**
- Modify: `src/tools/parliament.ts`

Currently, the tool only pulls from `owlRegistry` (hardcoded preferred names then all owls). When fewer than 2 owls exist in `owlRegistry`, use BMAD `SpecializedOwlSpec` entries from `specializedRegistry` to build synthetic `OwlInstance` objects.

- [ ] **Step 1: Understand the Parliament participant shape**

Parliament uses `OwlInstance[]`. From `src/owls/persona.ts`, an `OwlInstance` has:
- `persona: { name, type, emoji, challengeLevel, specialties, traits, systemPrompt, sourcePath }`
- `dna: OwlDNA` (from `createDefaultDNA`)
- optional `specialistPrompt`

We build synthetic instances for BMAD agents:
```typescript
{
  persona: {
    name: spec.name,
    type: spec.type,
    emoji: spec.emoji,
    challengeLevel: spec.personality.challengeLevel,
    specialties: spec.expertise,
    traits: [],
    systemPrompt: `${spec.role}. ${spec.additionalPrompt}`,
    sourcePath: "",
  },
  dna: createDefaultDNA(spec.name, spec.personality.challengeLevel),
  specialistPrompt: spec.additionalPrompt,
}
```

- [ ] **Step 2: Write the failing test**

Create `__tests__/tools/parliament-bmad.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { buildBmadParticipant } from "../../src/tools/parliament.js";
import type { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

describe("buildBmadParticipant", () => {
  it("converts a SpecializedOwlSpec into a valid OwlInstance-shaped object", () => {
    const spec: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Business Analyst",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: ["business analysis"],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: ["business"] },
      skills: { allowed: [] },
      additionalPrompt: "Identity: Channels Michael Porter.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    const instance = buildBmadParticipant(spec);
    expect(instance.persona.name).toBe("Mary");
    expect(instance.persona.emoji).toBe("📊");
    expect(instance.persona.specialties).toContain("business analysis");
    expect(instance.dna.owl).toBe("Mary");
    expect(instance.specialistPrompt).toContain("Michael Porter");
  });
});
```

- [ ] **Step 3: Run to verify it fails**

```bash
npx vitest run __tests__/tools/parliament-bmad.test.ts 2>&1 | tail -10
```

Expected: FAIL — `buildBmadParticipant` is not exported.

- [ ] **Step 4: Modify `src/tools/parliament.ts`**

Add imports at the top:
```typescript
import { createDefaultDNA } from "../owls/persona.js";
import type { OwlInstance } from "../owls/persona.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
```

Add the exported helper function before the `SummonParliamentTool` class:

```typescript
export function buildBmadParticipant(spec: SpecializedOwlSpec): OwlInstance {
  return {
    persona: {
      name: spec.name,
      type: spec.type,
      emoji: spec.emoji,
      challengeLevel: spec.personality.challengeLevel,
      specialties: spec.expertise,
      traits: [],
      systemPrompt: [spec.role, spec.additionalPrompt].filter(Boolean).join(". "),
      sourcePath: "",
    },
    dna: createDefaultDNA(spec.name, spec.personality.challengeLevel),
    specialistPrompt: spec.additionalPrompt || undefined,
  } as OwlInstance;
}
```

Inside `execute()`, after the block that builds `participants` from `preferredScns`, add:

```typescript
    // If owlRegistry is thin, supplement with BMAD agents from specializedRegistry
    if (participants.length < 2) {
      const specializedRegistry = context.engineContext.specializedRegistry ??
        (context.engineContext as any).ctx?.specializedRegistry;
      if (specializedRegistry) {
        const bmadAgents = specializedRegistry.listAll().filter(
          (s: SpecializedOwlSpec) => s.source === "bmad"
        );
        log.tool.debug("summon_parliament: supplementing with BMAD agents", { count: bmadAgents.length });
        for (const spec of bmadAgents.slice(0, 4)) {
          participants.push(buildBmadParticipant(spec));
        }
      }
    }
```

Also, pass `specializedRegistry` to `engineContext` — add a field lookup in the `execute()` method:

```typescript
    const { provider, config, pelletStore, owlRegistry } = context.engineContext;
    // Also grab specializedRegistry (available on engineContext in gateway runs)
    const specializedRegistry = (context.engineContext as any).specializedRegistry ??
      (context.engineContext as any).ctx?.specializedRegistry;
```

- [ ] **Step 5: Add `specializedRegistry` to `EngineContext` interface in `src/engine/runtime.ts`**

The `EngineContext` interface (line 47) does not currently have `specializedRegistry`. Add it:

```typescript
  /** Specialized owl registry — BMAD agents and custom specialists; available in gateway context */
  specializedRegistry?: import("../owls/specialized-registry.js").SpecializedOwlRegistry;
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/tools/parliament-bmad.test.ts
```

Expected: all tests PASS

- [ ] **Step 7: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep "parliament\|runtime"
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add src/tools/parliament.ts src/engine/runtime.ts __tests__/tools/parliament-bmad.test.ts
git commit -m "feat(parliament): use BMAD agents as participants when owlRegistry is thin"
```

---

### Task 6: Full test suite and integration verification

**Files:** None (verification only)

- [ ] **Step 1: Full TypeScript check**

```bash
npx tsc --noEmit 2>&1
```

Expected: 0 new errors.

- [ ] **Step 2: Run all affected tests**

```bash
npx vitest run __tests__/owls/bmad-agent-loader.test.ts __tests__/tools/parliament-bmad.test.ts __tests__/owls/helper-registry-compat.test.ts
```

Expected: all pass.

- [ ] **Step 3: Verify runtime registration via quick script**

```bash
node --input-type=module <<'EOF'
import { BmadAgentLoader } from "./src/owls/bmad-agent-loader.js";
const loader = new BmadAgentLoader();
const specs = await loader.loadAll();
console.log(`Loaded ${specs.length} BMAD agents:`);
for (const s of specs) console.log(`  ${s.emoji} ${s.name} — ${s.role} [${s.bmadSkillName}]`);
EOF
```

Expected: 6 lines, one per BMAD agent (Mary, Paige, John, Sally, Winston, Amelia).

- [ ] **Step 4: Run full test suite**

```bash
npm test 2>&1 | tail -20
```

Expected: same pass/fail as before (no regressions introduced).

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: BMAD Parliament integration complete — dynamic npm-package loading"
```
