# Architectural Cleanup & Instincts System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all architectural issues from the 2026-04-28 audit and implement the Instincts system as four sequential sub-projects.

**Architecture:** A (cleanup) → B (instincts) → C (routing extraction) → D (hardening). Each sub-project builds and tests pass before moving to the next.

**Tech Stack:** TypeScript ESM, Node.js ≥22, Vitest, gray-matter, existing `SpecializedOwlRegistry` pattern.

---

## File Map

| File | Change |
|------|--------|
| `__tests__/routing/secretary.test.ts` | Rewrite — remove db dependency, use folder registry |
| `src/engine/creative.ts` | Delete |
| `src/engine/manager.ts` | Delete |
| `src/clarification/mid-execution-router.ts` | Delete |
| `src/evolution/approval.ts` | Delete |
| `src/gateway/adapters/websocket.ts` | Delete |
| `src/heartbeat/idle-engine.ts` | Delete |
| `src/providers/minimax.ts` | Delete |
| `src/providers/ollama-native.ts` | Delete |
| `src/agent-watch/adapters/claude-code-mcp.ts` | Delete |
| `src/evolution/handler.ts` | Fix `require()` → static import |
| `src/compat/tools/browser.ts` | Fix `require()` → static import |
| `src/tools/computer-use/macos.ts` | Fix `require()` → static import |
| `src/instincts/types.ts` | Create — InstinctSpec interface |
| `src/instincts/registry.ts` | Create — InstinctRegistry |
| `src/instincts/engine.ts` | Create — InstinctEngine |
| `__tests__/instincts/registry.test.ts` | Create — registry tests |
| `__tests__/instincts/engine.test.ts` | Create — engine tests |
| `src/gateway/core.ts` | Wire instincts; extract routing block; add validateContext() |
| `src/gateway/handlers/routing-coordinator.ts` | Create — RoutingCoordinator |

---

## Sub-project A: Quick Cleanup

### Task A1: Rewrite SecretaryRouter tests to match new signature

**Files:**
- Modify: `__tests__/routing/secretary.test.ts`

The old tests used `new SecretaryRouter(db, undefined, classify)` with db owls. The implementation now uses `(folderRegistry, classify)` with folder specs only. The tests need to be completely rewritten.

- [ ] **Step 1: Replace the entire test file**

```typescript
import { describe, it, expect, vi } from "vitest";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { SecretaryRouter } from "../../src/routing/secretary.js";
import type { ClassifyFn } from "../../src/routing/llm-classifier.js";

function makeRegistry(specs: Array<{ name: string; role: string; expertise?: string[]; keywords?: string[] }>): SpecializedOwlRegistry {
  const registry = new SpecializedOwlRegistry();
  (registry as any).specs = new Map(
    specs.map((s) => [
      s.name.toLowerCase(),
      {
        name: s.name,
        role: s.role,
        emoji: "🦉",
        expertise: s.expertise ?? [],
        personality: { challengeLevel: "medium" as const, verbosity: "balanced" as const, tone: "neutral" },
        model: { provider: "", model: "" },
        permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
        routingRules: { keywords: s.keywords ?? [] },
        skills: { allowed: [] },
      },
    ]),
  );
  return registry;
}

function mockClassify(returnName: string | null): ClassifyFn {
  return vi.fn().mockResolvedValue(returnName);
}

describe("SecretaryRouter", () => {
  describe("route() — no specialists", () => {
    it("returns direct immediately when registry is empty", async () => {
      const classify = vi.fn();
      const router = new SecretaryRouter(makeRegistry([]), classify as ClassifyFn);

      const decision = await router.route("Hello", "user_test");

      expect(decision.type).toBe("direct");
      expect(decision.reason).toBe("No specialized owls configured");
      expect(classify).not.toHaveBeenCalled();
    });
  });

  describe("route() — LLM classify", () => {
    it("routes to folder specialist when LLM returns its name", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant" }]);
      const router = new SecretaryRouter(registry, mockClassify("TradingBot"));

      const decision = await router.route("I want to buy stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });

    it("returns direct when LLM returns null", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant" }]);
      const router = new SecretaryRouter(registry, mockClassify(null));

      const decision = await router.route("What is the weather?", "user_test");

      expect(decision.type).toBe("direct");
    });

    it("routes to parliament when LLM returns null and message triggers parliament", async () => {
      const registry = makeRegistry([{ name: "SomeOwl", role: "assistant" }]);
      const router = new SecretaryRouter(registry, mockClassify(null));

      const decision = await router.route(
        "Compare two programming languages: analyze the advantages and disadvantages, then evaluate the strategy for choosing one?",
        "user_test",
      );

      expect(decision.type).toBe("parliament");
    });

    it("falls back to direct when LLM classify throws", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant" }]);
      const broken: ClassifyFn = vi.fn().mockRejectedValue(new Error("LLM down"));
      const router = new SecretaryRouter(registry, broken);

      const decision = await router.route("I want to buy stocks", "user_test");

      expect(decision.type).toBe("direct");
    });
  });

  describe("route() — keyword fallback (no classify fn)", () => {
    it("routes to specialist whose keywords match the message", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading", keywords: ["stock", "trade", "portfolio"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("I want to buy some stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });

    it("returns direct when no keywords match", async () => {
      const registry = makeRegistry([{ name: "TradingBot", role: "trading", keywords: ["stock", "trade"] }]);
      const router = new SecretaryRouter(registry);

      const decision = await router.route("Tell me a joke", "user_test");

      expect(decision.type).toBe("direct");
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
npx vitest run __tests__/routing/secretary.test.ts
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add __tests__/routing/secretary.test.ts
git commit -m "test: rewrite SecretaryRouter tests for folder-registry-only signature"
```

---

### Task A2: Delete 10 dead files

**Files:** 10 deletions, no modifications.

- [ ] **Step 1: Verify no imports reference these files**

```bash
grep -r "engine/creative\|engine/manager\|mid-execution-router\|evolution/approval\|adapters/websocket\|idle-engine\|providers/minimax\|providers/ollama-native\|claude-code-mcp" src/ --include="*.ts" -l
```

Expected: no output (zero matches).

- [ ] **Step 2: Delete all dead files**

```bash
rm src/engine/creative.ts \
   src/engine/manager.ts \
   src/clarification/mid-execution-router.ts \
   src/evolution/approval.ts \
   src/gateway/adapters/websocket.ts \
   src/heartbeat/idle-engine.ts \
   src/providers/minimax.ts \
   src/providers/ollama-native.ts \
   src/agent-watch/adapters/claude-code-mcp.ts
```

- [ ] **Step 3: Build to verify nothing broke**

```bash
npm run build
```

Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete 9 dead unreferenced classes"
```

---

### Task A3: Fix require() violations

**Files:**
- Modify: `src/evolution/handler.ts:132`
- Modify: `src/compat/tools/browser.ts:44`
- Modify: `src/tools/computer-use/macos.ts:735-736`

- [ ] **Step 1: Fix `src/evolution/handler.ts`**

Find the line:
```typescript
const fs = require("node:fs");
if (fs.existsSync(indicator)) {
```

Replace with:
```typescript
const { existsSync } = await import("node:fs");
if (existsSync(indicator)) {
```

- [ ] **Step 2: Fix `src/compat/tools/browser.ts`**

Find the line:
```typescript
const { readdirSync } = require("node:fs");
```

Add a static import at the top of the file (after existing imports):
```typescript
import { readdirSync } from "node:fs";
```

Then delete the `require()` line.

- [ ] **Step 3: Fix `src/tools/computer-use/macos.ts`**

Find the lines:
```typescript
const { existsSync, statSync } = require("node:fs");
const { basename, resolve } = require("node:path");
```

Add static imports at the top of the file (after existing imports):
```typescript
import { existsSync, statSync } from "node:fs";
import { basename, resolve } from "node:path";
```

Then delete both `require()` lines.

- [ ] **Step 4: Build to verify no TypeScript errors**

```bash
npm run build
```

Expected: exits 0.

- [ ] **Step 5: Run full test suite**

```bash
npm test 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/evolution/handler.ts src/compat/tools/browser.ts src/tools/computer-use/macos.ts
git commit -m "fix: convert require() to ESM imports in handler, browser, macos"
```

---

## Sub-project B: Instincts System

### Task B1: Define InstinctSpec type

**Files:**
- Create: `src/instincts/types.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/instincts/registry.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rm, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { InstinctRegistry } from "../../src/instincts/registry.js";

const testOwlsDir = join(__dirname, ".test_instincts_owls");
const owlName = "historyMan";

async function writeInstinct(name: string, description: string, constraint: string) {
  const dir = join(testOwlsDir, owlName, "instincts");
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, `${name}.md`), `---\nname: ${name}\ndescription: ${description}\nconstraint: ${constraint}\n---\n`);
}

beforeEach(async () => {
  await rm(testOwlsDir, { recursive: true, force: true });
  await mkdir(testOwlsDir, { recursive: true });
});

afterEach(async () => {
  await rm(testOwlsDir, { recursive: true, force: true });
});

describe("InstinctRegistry", () => {
  it("loads instincts from owl instincts/ folder", async () => {
    await writeInstinct("no-speculation", "user asks for future predictions", "Do not speculate.");
    const registry = new InstinctRegistry();
    await registry.loadForOwl(testOwlsDir, owlName);
    const instincts = registry.get(owlName);
    expect(instincts).toHaveLength(1);
    expect(instincts[0].name).toBe("no-speculation");
    expect(instincts[0].constraint).toBe("Do not speculate.");
  });

  it("returns empty array when owl has no instincts folder", async () => {
    const registry = new InstinctRegistry();
    await registry.loadForOwl(testOwlsDir, owlName);
    expect(registry.get(owlName)).toHaveLength(0);
  });

  it("loads multiple instincts", async () => {
    await writeInstinct("no-speculation", "future predictions", "No speculation.");
    await writeInstinct("stay-in-domain", "off-topic questions", "Stay in domain.");
    const registry = new InstinctRegistry();
    await registry.loadForOwl(testOwlsDir, owlName);
    expect(registry.get(owlName)).toHaveLength(2);
  });

  it("clear() removes cached instincts for an owl", async () => {
    await writeInstinct("no-speculation", "future predictions", "No speculation.");
    const registry = new InstinctRegistry();
    await registry.loadForOwl(testOwlsDir, owlName);
    registry.clear(owlName);
    expect(registry.get(owlName)).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npx vitest run __tests__/instincts/registry.test.ts
```

Expected: FAIL — `Cannot find module '../../src/instincts/registry.js'`

- [ ] **Step 3: Create `src/instincts/types.ts`**

```typescript
export interface InstinctSpec {
  name: string;
  description: string;
  constraint: string;
  owlName: string;
}
```

- [ ] **Step 4: Create `src/instincts/registry.ts`**

```typescript
import { readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import matter from "gray-matter";
import type { InstinctSpec } from "./types.js";
import { log } from "../logger.js";

export class InstinctRegistry {
  private cache: Map<string, InstinctSpec[]> = new Map();

  async loadForOwl(owlsDir: string, owlName: string): Promise<void> {
    const instinctsDir = join(owlsDir, owlName, "instincts");
    if (!existsSync(instinctsDir)) {
      this.cache.set(owlName, []);
      return;
    }

    let files: string[];
    try {
      files = (await readdir(instinctsDir)).filter((f) => f.endsWith(".md"));
    } catch {
      this.cache.set(owlName, []);
      return;
    }

    const instincts: InstinctSpec[] = [];
    for (const file of files) {
      try {
        const raw = await readFile(join(instinctsDir, file), "utf-8");
        const { data } = matter(raw);
        if (data.name && data.description && data.constraint) {
          instincts.push({
            name: String(data.name),
            description: String(data.description),
            constraint: String(data.constraint),
            owlName,
          });
        }
      } catch (err) {
        log.engine.warn(`[InstinctRegistry] Failed to parse ${file}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }

    this.cache.set(owlName, instincts);
    log.engine.info(`[InstinctRegistry] Loaded ${instincts.length} instincts for ${owlName}`);
  }

  get(owlName: string): InstinctSpec[] {
    return this.cache.get(owlName) ?? [];
  }

  clear(owlName: string): void {
    this.cache.delete(owlName);
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/instincts/registry.test.ts
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/instincts/types.ts src/instincts/registry.ts __tests__/instincts/registry.test.ts
git commit -m "feat: add InstinctRegistry — loads instinct specs from owl instincts/ folder"
```

---

### Task B2: InstinctEngine — LLM classifier

**Files:**
- Create: `src/instincts/engine.ts`
- Create: `__tests__/instincts/engine.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/instincts/engine.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { InstinctEngine } from "../../src/instincts/engine.js";
import type { InstinctSpec } from "../../src/instincts/types.js";

function makeInstinct(name: string, description: string, constraint: string): InstinctSpec {
  return { name, description, constraint, owlName: "testOwl" };
}

function makeProvider(jsonResponse: string) {
  return {
    chat: vi.fn().mockResolvedValue({ content: jsonResponse }),
  } as any;
}

describe("InstinctEngine", () => {
  it("returns empty array when instincts list is empty (no LLM call)", async () => {
    const provider = { chat: vi.fn() } as any;
    const engine = new InstinctEngine();
    const result = await engine.evaluate("Hello", [], provider, "gpt-4");
    expect(result).toHaveLength(0);
    expect(provider.chat).not.toHaveBeenCalled();
  });

  it("returns constraint strings for fired instincts", async () => {
    const instincts = [
      makeInstinct("no-spec", "predicts future", "Do not speculate."),
      makeInstinct("stay-domain", "off topic", "Stay in domain."),
    ];
    const provider = makeProvider('["no-spec"]');
    const engine = new InstinctEngine();
    const result = await engine.evaluate("What will happen tomorrow?", instincts, provider, "gpt-4");
    expect(result).toEqual(["Do not speculate."]);
  });

  it("returns empty array when LLM says no instincts fire", async () => {
    const instincts = [makeInstinct("no-spec", "predicts future", "Do not speculate.")];
    const provider = makeProvider("[]");
    const engine = new InstinctEngine();
    const result = await engine.evaluate("Tell me about history", instincts, provider, "gpt-4");
    expect(result).toHaveLength(0);
  });

  it("returns empty array when LLM returns invalid JSON", async () => {
    const instincts = [makeInstinct("no-spec", "predicts future", "Do not speculate.")];
    const provider = makeProvider("not valid json");
    const engine = new InstinctEngine();
    const result = await engine.evaluate("Hello", instincts, provider, "gpt-4");
    expect(result).toHaveLength(0);
  });

  it("returns empty array when LLM call throws", async () => {
    const instincts = [makeInstinct("no-spec", "predicts future", "Do not speculate.")];
    const provider = { chat: vi.fn().mockRejectedValue(new Error("LLM down")) } as any;
    const engine = new InstinctEngine();
    const result = await engine.evaluate("Hello", instincts, provider, "gpt-4");
    expect(result).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

```bash
npx vitest run __tests__/instincts/engine.test.ts
```

Expected: FAIL — `Cannot find module '../../src/instincts/engine.js'`

- [ ] **Step 3: Create `src/instincts/engine.ts`**

```typescript
import type { InstinctSpec } from "./types.js";
import type { BaseProvider } from "../providers/base.js";
import { log } from "../logger.js";

export class InstinctEngine {
  async evaluate(
    message: string,
    instincts: InstinctSpec[],
    provider: BaseProvider,
    model: string,
  ): Promise<string[]> {
    if (instincts.length === 0) return [];

    const list = instincts
      .map((inst) => `- name: "${inst.name}" | triggers when: ${inst.description}`)
      .join("\n");

    const prompt = `You are evaluating which behavioral constraints apply to a user message.

Instincts:
${list}

User message: "${message}"

Return a JSON array of instinct names that apply to this message. Return [] if none apply.
Example: ["no-speculation"] or []
Return ONLY the JSON array, nothing else.`;

    try {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        { model, maxTokens: 200 },
      );
      const fired: string[] = JSON.parse(response.content);
      if (!Array.isArray(fired)) return [];
      return instincts
        .filter((inst) => fired.includes(inst.name))
        .map((inst) => inst.constraint);
    } catch (err) {
      log.engine.warn(`[InstinctEngine] Evaluation failed: ${err instanceof Error ? err.message : String(err)}`);
      return [];
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/instincts/engine.test.ts
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/instincts/engine.ts __tests__/instincts/engine.test.ts
git commit -m "feat: add InstinctEngine — LLM-based instinct evaluation"
```

---

### Task B3: Wire instincts into gateway

**Files:**
- Modify: `src/gateway/core.ts`

The gateway needs to:
1. Hold `InstinctRegistry` and `InstinctEngine` instances
2. Load instincts for the active owl after routing resolves
3. Inject fired constraints as `engineCtx.systemPromptPrefix`

- [ ] **Step 1: Add imports to `src/gateway/core.ts`**

After the existing imports block, add:
```typescript
import { InstinctRegistry } from "../instincts/registry.js";
import { InstinctEngine } from "../instincts/engine.js";
```

- [ ] **Step 2: Add private fields to `OwlGateway` class**

In the private fields section (around line 163, after `private secretaryRouter`):
```typescript
private instinctRegistry: InstinctRegistry | null = null;
private instinctEngine: InstinctEngine | null = null;
```

- [ ] **Step 3: Initialize in constructor**

After the SpecializedOwlRegistry initialization block (around line 470), add:
```typescript
// Auto-initialize InstinctRegistry and InstinctEngine
this.instinctRegistry = new InstinctRegistry();
this.instinctEngine = new InstinctEngine();
log.engine.info("[instincts] InstinctRegistry and InstinctEngine initialized");
```

- [ ] **Step 4: Load instincts for active owl after routing resolves**

In the `handleMessage` method, after the routing block sets `activeOwlName` (after line ~1730, after `// ─── Tag response with #OwlName` comment block header, before the engine runs), add:

```typescript
// ─── Instinct evaluation — inject constraints for active owl ───────
if (this.instinctRegistry && this.instinctEngine && this.ctx.provider) {
  const workspacePath = this.ctx.cwd ?? process.cwd();
  const owlsDir = join(workspacePath, "owls");
  await this.instinctRegistry.loadForOwl(owlsDir, activeOwlName);
  const instincts = this.instinctRegistry.get(activeOwlName);
  if (instincts.length > 0) {
    const constraints = await this.instinctEngine.evaluate(
      text,
      instincts,
      this.ctx.provider,
      this.ctx.config.defaultModel,
    );
    if (constraints.length > 0) {
      engineCtx.systemPromptPrefix = constraints.join("\n");
      log.engine.info(`[instincts] Injected ${constraints.length} constraint(s) for ${activeOwlName}`);
    }
  }
}
```

- [ ] **Step 5: Also reload instincts in `reloadSpecializedRegistry()`**

Find the existing `reloadSpecializedRegistry()` method and update it:
```typescript
async reloadSpecializedRegistry(): Promise<void> {
  if (!this.ctx.specializedRegistry) return;
  const workspacePath = this.ctx.cwd ?? process.cwd();
  await this.ctx.specializedRegistry.loadAll(workspacePath);
  this.instinctRegistry = new InstinctRegistry();
  log.engine.info("[instincts] InstinctRegistry cleared on registry reload");
}
```

- [ ] **Step 6: Build to verify no TypeScript errors**

```bash
npm run build
```

Expected: exits 0.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat: wire InstinctRegistry and InstinctEngine into gateway"
```

---

## Sub-project C: Extract RoutingCoordinator

### Task C1: Create RoutingCoordinator

**Files:**
- Create: `src/gateway/handlers/routing-coordinator.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Read the exact routing block in core.ts**

The routing block starts at the comment `// ─── Epic 2: Secretary Router` and ends just before `// ─── Tag response with #OwlName`. Read `src/gateway/core.ts` lines 1652–1775 to get the exact code.

- [ ] **Step 2: Create `src/gateway/handlers/routing-coordinator.ts`**

```typescript
import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SecretaryRouter } from "../../routing/secretary.js";
import type { MultiRoundDebateManager } from "../../parliament/multi-round-debate.js";
import type { OwlRegistry } from "../../owls/registry.js";
import type { GatewayCallbacks } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { GatewayMessage } from "../types.js";
import { log } from "../../logger.js";
import { join } from "node:path";

export interface RoutingResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

export class RoutingCoordinator {
  constructor(
    private specializedRegistry: SpecializedOwlRegistry | undefined,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private multiRoundDebate: MultiRoundDebateManager | null,
    private owlRegistry: { get(name: string): any; getDefault(): any } | undefined,
    private defaultOwlName: string,
    private workspacePath: string,
  ) {}

  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<RoutingResult> {
    let activeOwlName = this.defaultOwlName;

    // ─── Explicit @mention ──────────────────────────────────────
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    if (explicitMention && this.specializedRegistry) {
      const [, owlName, remainingMessage] = explicitMention;
      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = remainingMessage?.trim() || "Hello";
        const baseOwl = this.owlRegistry?.getDefault() ?? engineCtx.owl;
        const specialistPrompt = [
          `You are ${spec.name}, ${spec.role}.`,
          spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
          `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
          spec.permissions.capabilityConstraints.length > 0
            ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.`
            : "",
        ].filter(Boolean).join(" ");
        engineCtx.owl = {
          ...baseOwl,
          specialistPrompt,
          specialistRoutingRules: spec.routingRules.keywords,
          specialistPermissions: spec.permissions,
        };
        engineCtx.specialistPrompt = specialistPrompt;
        activeOwlName = spec.name;
        callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
        log.engine.info(`[RoutingCoordinator] @mention → "${spec.name}"`);
      } else {
        log.engine.warn(`[RoutingCoordinator] @mention "${owlName}" not found in registry`);
      }
    }

    // ─── SecretaryRouter implicit routing ───────────────────────
    if (this.specializedRegistry && message.userId && activeOwlName === this.defaultOwlName) {
      const router = this.getSecretaryRouter();
      if (!router) {
        log.engine.warn("[RoutingCoordinator] SecretaryRouter not available — skipping specialist routing");
        return { text, activeOwlName, parliamentHandled: false };
      }

      const routingDecision = await router.route(text, message.userId);

      if (routingDecision.type === "specialist") {
        const specializedOwl = routingDecision.owl;
        const spec = this.specializedRegistry.get(specializedOwl.name);
        const baseOwl = this.owlRegistry?.get(specializedOwl.name)
          ?? this.owlRegistry?.getDefault()
          ?? engineCtx.owl;
        engineCtx.owl = {
          ...baseOwl,
          specialistPrompt: specializedOwl.personalityPrompt,
          specialistRoutingRules: specializedOwl.routingRules,
          specialistPermissions: spec?.permissions,
        };
        engineCtx.specialistPrompt = specializedOwl.personalityPrompt;
        activeOwlName = specializedOwl.name;
        callbacks?.onOwlChange?.(spec?.emoji || "🦉", specializedOwl.name);
        log.engine.info(`[RoutingCoordinator] Routed to "${specializedOwl.name}"`);
      } else if (routingDecision.type === "parliament") {
        log.engine.info(`[RoutingCoordinator] Parliament triggered`);
        return { text, activeOwlName, parliamentHandled: true };
      }
    } else if (!this.specializedRegistry && message.userId && activeOwlName === this.defaultOwlName) {
      log.engine.warn("[RoutingCoordinator] specializedRegistry not loaded — specialist routing skipped");
    }

    return { text, activeOwlName, parliamentHandled: false };
  }
}
```

- [ ] **Step 3: Add import and field in `src/gateway/core.ts`**

Add import:
```typescript
import { RoutingCoordinator } from "./handlers/routing-coordinator.js";
```

Add private field (after `private secretaryRouter`):
```typescript
private routingCoordinator: RoutingCoordinator | null = null;
```

- [ ] **Step 4: Initialize RoutingCoordinator in constructor**

After the instincts initialization block, add:
```typescript
this.routingCoordinator = new RoutingCoordinator(
  ctx.specializedRegistry,
  () => this.secretaryRouter,
  this.multiRoundDebate,
  ctx.owlRegistry,
  ctx.owl.persona.name,
  ctx.cwd ?? process.cwd(),
);
```

- [ ] **Step 5: Replace the routing block in handleMessage with a single call**

Find the block starting at `// ─── Epic 2: Secretary Router` and ending before the parliament execution block. Replace it with:

```typescript
// ─── Routing — @mention + SecretaryRouter ────────────────────
let activeOwlName = this.ctx.owl.persona.name;
let routingResult: import("./handlers/routing-coordinator.js").RoutingResult | null = null;
if (this.routingCoordinator) {
  routingResult = await this.routingCoordinator.resolve(text, message, engineCtx, callbacks);
  text = routingResult.text;
  activeOwlName = routingResult.activeOwlName;
}
```

Handle parliament separately — check `routingResult?.parliamentHandled` before proceeding to the parliament execution block.

- [ ] **Step 6: Build and run full test suite**

```bash
npm run build && npm test 2>&1 | tail -15
```

Expected: build exits 0, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/handlers/routing-coordinator.ts src/gateway/core.ts
git commit -m "refactor: extract RoutingCoordinator from gateway/core.ts"
```

---

## Sub-project D: Silent Failure Hardening

### Task D1: Add validateContext() and routing guard warnings

**Files:**
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Add `validateContext()` method to `OwlGateway`**

Add this private method after the constructor closing brace:

```typescript
private validateContext(): void {
  if (!this.ctx.specializedRegistry)
    log.engine.warn("[Gateway] specializedRegistry is null — @mention and specialist routing disabled");
  if (!this.multiRoundDebate)
    log.engine.warn("[Gateway] multiRoundDebate is null — Parliament feature disabled");
  if (!this.ctx.pelletStore)
    log.engine.warn("[Gateway] pelletStore is null — Knowledge pellet generation disabled");
  if (!this.ctx.owlRegistry)
    log.engine.warn("[Gateway] owlRegistry is null — Multi-owl features disabled");
}
```

- [ ] **Step 2: Call validateContext() at end of constructor**

At the very end of the constructor body, before the closing brace, add:
```typescript
this.validateContext();
```

- [ ] **Step 3: Add parliament null-guard warning**

Find the parliament execution block in `handleMessage` — the `if (this.multiRoundDebate && this.ctx.owlRegistry && ...)` check. Add an else branch:

```typescript
} else {
  log.engine.warn("[Gateway] Parliament triggered but multiRoundDebate module is null — falling back to direct");
}
```

- [ ] **Step 4: Build and run full test suite**

```bash
npm run build && npm test 2>&1 | tail -15
```

Expected: build exits 0, all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat: add validateContext() startup warnings and parliament null-guard"
```

---

## Self-Review Notes

**Spec coverage check:**
- A1 (SecretaryRouter tests) ✓ Task A1
- A2 (delete dead files) ✓ Task A2
- A3 (require() fixes) ✓ Task A3
- B (instincts system — types, registry, engine, gateway wiring, reload) ✓ Tasks B1-B3
- C (RoutingCoordinator extraction) ✓ Task C1
- D (validateContext, routing guard, parliament guard) ✓ Task D1

**Type consistency:**
- `InstinctSpec` defined in B1 Step 3, used in B2 Step 3 and B3 Step 4 ✓
- `RoutingResult` defined in C1 Step 2, used in C1 Step 5 ✓
- `InstinctRegistry.loadForOwl(owlsDir, owlName)` signature consistent across B1 and B3 ✓
- `InstinctEngine.evaluate(message, instincts, provider, model)` consistent across B2 and B3 ✓
