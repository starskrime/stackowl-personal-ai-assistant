# Specialized Owl Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify all owl definitions under a single `SpecializedOwlRegistry`, add coordinator/specialist typing, fix the `RoutingDecision` db-type leak, enable session-pinned specialist routing with file-based persistence, and inject pellet memory context when a specialist activates.

**Architecture:** `SpecializedOwlSpec` gains `type` + `additionalPrompt`; the registry gains `getDefault()`/`listSpecialists()`/DNA persistence; `SecretaryRouter` drops the `SpecializedOwl` db shim; `RoutingCoordinator` checks session pin first, builds a layered specialist prompt, and injects past pellet context. A new `SessionStateStore` persists the active owl across restarts. `OwlRegistry` deletion is deferred to Phase 2 (see note at bottom).

**Tech Stack:** TypeScript, gray-matter, better-sqlite3, Node.js fs/promises, vitest

**Spec:** `docs/superpowers/specs/2026-04-28-specialized-owl-architecture.md`

---

## File Map

| File | Change |
|---|---|
| `src/owls/specialized-types.ts` | Add `type`, `additionalPrompt`, `folderPath` to `SpecializedOwlSpec` |
| `src/owls/specialized-parser.ts` | Read `type` from frontmatter + body as `additionalPrompt` |
| `src/owls/specialized-registry.ts` | Add `getDefault()`, `listSpecialists()`, DNA load/save, `folderPath` tracking |
| `src/routing/secretary.ts` | Replace `SpecializedOwl` (db type) with `SpecializedOwlSpec`; delete `specToSyntheticOwl()` |
| `src/gateway/handlers/routing-coordinator.ts` | Simplify constructor; add `buildSpecialistPrompt()`; add session pin check; add memory injection |
| `src/memory/store.ts` | Add `activeOwlName?: string` to `Session.metadata` |
| `src/routing/session-state.ts` | **New** — `SessionStateStore`: load/save/clear `{userId}.json` |
| `src/gateway/core.ts` | Update `RoutingCoordinator` construction; pass `session` to `resolve()`; wire `SessionStateStore` |
| `__tests__/owls/specialized-parser.test.ts` | Add tests for `type` parsing and `additionalPrompt` |
| `__tests__/owls/specialized-registry.test.ts` | Add tests for `getDefault()`, `listSpecialists()`, DNA |
| `__tests__/routing/secretary.test.ts` | Update helper to include `type`; assert `owl` is `SpecializedOwlSpec` |
| `__tests__/routing/session-state.test.ts` | **New** — tests for `SessionStateStore` |

---

## Task 1: Add `type`, `additionalPrompt`, `folderPath` to `SpecializedOwlSpec`

**Files:**
- Modify: `src/owls/specialized-types.ts`
- Modify: `src/owls/specialized-parser.ts`
- Modify: `__tests__/owls/specialized-parser.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `__tests__/owls/specialized-parser.test.ts`:

```typescript
it("defaults to type 'specialist' when type field is absent", () => {
  const content = `---\nname: TestOwl\nrole: Test\n---\n`;
  const spec = parseSpecializedOwl(content);
  expect(spec.type).toBe("specialist");
});

it("parses type: coordinator", () => {
  const content = `---\nname: Noctua\ntype: coordinator\nrole: Chief of Staff\n---\n`;
  const spec = parseSpecializedOwl(content);
  expect(spec.type).toBe("coordinator");
});

it("parses markdown body as additionalPrompt", () => {
  const content = `---\nname: TestOwl\nrole: Test\n---\n\nYou are a test owl with special powers.`;
  const spec = parseSpecializedOwl(content);
  expect(spec.additionalPrompt).toBe("You are a test owl with special powers.");
});

it("sets additionalPrompt to empty string when body is empty", () => {
  const content = `---\nname: TestOwl\nrole: Test\n---\n`;
  const spec = parseSpecializedOwl(content);
  expect(spec.additionalPrompt).toBe("");
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/owls/specialized-parser.test.ts
```
Expected: FAIL — `spec.type` and `spec.additionalPrompt` are undefined.

- [ ] **Step 3: Update `SpecializedOwlSpec` type**

Replace the `SpecializedOwlSpec` interface in `src/owls/specialized-types.ts`:

```typescript
export interface SpecializedOwlSpec {
  name: string;
  type: "coordinator" | "specialist";
  role: string;
  emoji: string;
  personality: SpecializedPersonality;
  expertise: string[];
  model: SpecializedModel;
  permissions: SpecializedPermissions;
  routingRules: SpecializedRoutingRules;
  skills: SpecializedSkills;
  additionalPrompt: string;
  folderPath?: string;
  credentialsPath?: string;
}
```

- [ ] **Step 4: Update `parseSpecializedOwl` to read body + type**

In `src/owls/specialized-parser.ts`, change the destructuring and return:

```typescript
export function parseSpecializedOwl(content: string): SpecializedOwlSpec {
  const { data, content: body } = matter(content);

  if (!data.name || typeof data.name !== "string" || !data.name.trim()) {
    throw new Error("parseSpecializedOwl: missing required field: name");
  }

  const type: "coordinator" | "specialist" =
    data.type === "coordinator" ? "coordinator" : "specialist";

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
    type,
    role: (data.role as string) ?? "",
    emoji: (data.emoji as string) ?? "🦉",
    personality,
    expertise: Array.isArray(data.domains) ? data.domains : [],
    model,
    permissions,
    routingRules,
    skills,
    additionalPrompt: body.trim(),
  };
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/owls/specialized-parser.test.ts
```
Expected: all PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/owls/specialized-types.ts src/owls/specialized-parser.ts __tests__/owls/specialized-parser.test.ts
git commit -m "feat: add type, additionalPrompt, folderPath to SpecializedOwlSpec"
```

---

## Task 2: Add `getDefault()`, `listSpecialists()`, and `folderPath` to `SpecializedOwlRegistry`

**Files:**
- Modify: `src/owls/specialized-registry.ts`
- Modify: `__tests__/owls/specialized-registry.test.ts`
- Modify: `__tests__/owls/test-workspace/owls/TradingBot/specialized_owl.md` (add type field)

- [ ] **Step 1: Write failing tests**

Add to `__tests__/owls/specialized-registry.test.ts`:

```typescript
it("getDefault() returns the coordinator owl", async () => {
  await registry.loadAll(testWorkspace);
  // TradingBot has type: coordinator in test-workspace (see Step 2 below)
  const defaultOwl = registry.getDefault();
  expect(defaultOwl).toBeDefined();
  expect(defaultOwl?.type).toBe("coordinator");
});

it("listSpecialists() returns only specialist owls", async () => {
  await registry.loadAll(testWorkspace);
  const specialists = registry.listSpecialists();
  expect(specialists.every((s) => s.type === "specialist")).toBe(true);
});

it("folderPath is set on each loaded owl", async () => {
  await registry.loadAll(testWorkspace);
  const owl = registry.get("tradingbot");
  expect(owl?.folderPath).toBeDefined();
  expect(owl?.folderPath).toContain("TradingBot");
});
```

- [ ] **Step 2: Add `type: coordinator` to the test workspace owl**

Edit `__tests__/owls/test-workspace/owls/TradingBot/specialized_owl.md` to add `type: coordinator` in the frontmatter (we use this owl as the coordinator in tests):

```yaml
---
name: TradingBot
type: coordinator
role: Stock trading assistant
...
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
npx vitest run __tests__/owls/specialized-registry.test.ts
```
Expected: FAIL — `getDefault`, `listSpecialists`, `folderPath` not implemented.

- [ ] **Step 4: Update `SpecializedOwlRegistry`**

Full replacement of `src/owls/specialized-registry.ts`:

```typescript
import { readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { SpecializedOwlSpec } from "./specialized-types.js";
import type { OwlDNA } from "./persona.js";
import { parseSpecializedOwl } from "./specialized-parser.js";
import { log } from "../logger.js";

export class SpecializedOwlRegistry {
  private specs: Map<string, SpecializedOwlSpec> = new Map();

  async loadAll(workspacePath: string): Promise<void> {
    this.specs.clear();
    const owlsDir = join(workspacePath, "owls");
    if (!existsSync(owlsDir)) {
      log.engine.info("[SpecializedOwlRegistry] No owls directory found");
      return;
    }

    let entries: string[];
    try {
      const dirEntries = await readdir(owlsDir, { withFileTypes: true });
      entries = dirEntries.filter((e) => e.isDirectory()).map((e) => e.name);
    } catch {
      return;
    }

    for (const entry of entries) {
      const specPath = join(owlsDir, entry, "specialized_owl.md");
      if (!existsSync(specPath)) continue;

      try {
        const raw = await readFile(specPath, "utf-8");
        const spec = parseSpecializedOwl(raw);
        spec.folderPath = join(owlsDir, entry);
        spec.credentialsPath = join(owlsDir, entry, "credentials");

        // Load DNA from owl_dna.json if it exists
        const dnaPath = join(owlsDir, entry, "owl_dna.json");
        if (existsSync(dnaPath)) {
          try {
            const rawDna = await readFile(dnaPath, "utf-8");
            (spec as SpecializedOwlSpec & { dna?: OwlDNA }).dna = JSON.parse(rawDna) as OwlDNA;
          } catch {
            log.engine.warn(`[SpecializedOwlRegistry] Corrupt DNA for ${spec.name}`);
          }
        }

        this.specs.set(spec.name.toLowerCase(), spec);
        log.engine.info(`[SpecializedOwlRegistry] Loaded ${spec.name} (${spec.type})`);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        log.engine.warn(`[SpecializedOwlRegistry] Failed to load ${entry}: ${msg}`);
      }
    }
  }

  get(name: string): SpecializedOwlSpec | undefined {
    const lower = name.toLowerCase();
    const exact = this.specs.get(lower);
    if (exact) return exact;
    for (const [key, spec] of this.specs) {
      if (key.startsWith(lower)) return spec;
    }
    return undefined;
  }

  /** Returns the coordinator owl, or the first owl if no coordinator is defined. */
  getDefault(): SpecializedOwlSpec | undefined {
    for (const spec of this.specs.values()) {
      if (spec.type === "coordinator") return spec;
    }
    const first = this.specs.values().next();
    return first.done ? undefined : first.value;
  }

  /** Returns all specialist (non-coordinator) owls. */
  listSpecialists(): SpecializedOwlSpec[] {
    return this.listAll().filter((s) => s.type === "specialist");
  }

  listAll(): SpecializedOwlSpec[] {
    return Array.from(this.specs.values());
  }

  getByExpertise(domain: string): SpecializedOwlSpec[] {
    const lower = domain.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.expertise.some((e) => e.toLowerCase().includes(lower)),
    );
  }

  getByKeyword(keyword: string): SpecializedOwlSpec[] {
    const lower = keyword.toLowerCase();
    return this.listAll().filter((spec) =>
      spec.routingRules.keywords.some((k) => k.toLowerCase().includes(lower)),
    );
  }

  async saveDNA(owlName: string, dna: OwlDNA): Promise<void> {
    const spec = this.specs.get(owlName.toLowerCase());
    if (!spec?.folderPath) return;
    const dnaPath = join(spec.folderPath, "owl_dna.json");
    await writeFile(dnaPath, JSON.stringify(dna, null, 2), "utf-8");
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run __tests__/owls/specialized-registry.test.ts
```
Expected: all PASS.

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/owls/specialized-registry.ts __tests__/owls/specialized-registry.test.ts __tests__/owls/test-workspace/owls/TradingBot/specialized_owl.md
git commit -m "feat: add getDefault, listSpecialists, DNA persistence, folderPath to SpecializedOwlRegistry"
```

---

## Task 3: Fix `RoutingDecision` — replace `SpecializedOwl` db type with `SpecializedOwlSpec`

**Files:**
- Modify: `src/routing/secretary.ts`
- Modify: `__tests__/routing/secretary.test.ts`

- [ ] **Step 1: Update `makeRegistry` helper in the test to include `type`**

In `__tests__/routing/secretary.test.ts`, update the specs mock to include `type`:

```typescript
function makeRegistry(specs: Array<{ name: string; role: string; expertise?: string[]; keywords?: string[]; type?: "coordinator" | "specialist" }>): SpecializedOwlRegistry {
  const registry = new SpecializedOwlRegistry();
  (registry as any).specs = new Map(
    specs.map((s) => [
      s.name.toLowerCase(),
      {
        name: s.name,
        type: s.type ?? "specialist",
        role: s.role,
        emoji: "🦉",
        expertise: s.expertise ?? [],
        personality: { challengeLevel: "medium" as const, verbosity: "balanced" as const, tone: "neutral" },
        model: { provider: "", model: "" },
        permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
        routingRules: { keywords: s.keywords ?? [] },
        skills: { allowed: [] },
        additionalPrompt: "",
      },
    ]),
  );
  return registry;
}
```

Also add this assertion to the existing LLM routing test:

```typescript
it("specialist decision carries SpecializedOwlSpec (not db SpecializedOwl)", async () => {
  const registry = makeRegistry([{ name: "TradingBot", role: "trading assistant" }]);
  const router = new SecretaryRouter(registry, mockClassify("TradingBot"));

  const decision = await router.route("I want to buy stocks", "user_test");

  expect(decision.type).toBe("specialist");
  if (decision.type === "specialist") {
    // SpecializedOwlSpec has these fields; SpecializedOwl (db) does not
    expect(decision.owl.type).toBeDefined();
    expect(decision.owl.additionalPrompt).toBeDefined();
    expect((decision.owl as any).ownerId).toBeUndefined(); // db field must be gone
  }
});
```

- [ ] **Step 2: Run tests to verify the new assertion fails**

```bash
npx vitest run __tests__/routing/secretary.test.ts
```
Expected: the new `SpecializedOwlSpec` assertion FAILs (owl still has `ownerId`).

- [ ] **Step 3: Rewrite `secretary.ts`**

Full replacement of `src/routing/secretary.ts`:

```typescript
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { ClassifyFn } from "./llm-classifier.js";
import { log } from "../logger.js";

const MIN_MESSAGE_LENGTH = 10;
const ROUTING_CONFIDENCE_THRESHOLD = 0.4;
const MATCH_SCORE_THRESHOLD = 0.25;
const MATCH_WEIGHT = 0.7;
const DNA_WEIGHT = 0.3;

const PARLIAMENT_KEYWORDS = [
  "compare", "versus", "vs", "difference between",
  "pros and cons", "advantages and disadvantages",
  "should we", "should i", "decision", "choose between",
  "analyze", "analysis", "evaluate", "assessment",
  "strategy", "strategic", "planning", "plan",
  "architecture", "design", "system design",
] as const;

export type RoutingDecision =
  | { type: "direct"; reason: string }
  | { type: "specialist"; owl: SpecializedOwlSpec; reason: string }
  | { type: "parliament"; reason: string };

interface RoutingTarget {
  name: string;
  routingRules: string[];
  expertiseDomains?: string[];
}

export class SecretaryRouter {
  private folderRegistry?: SpecializedOwlRegistry;
  private classify?: ClassifyFn;

  constructor(
    folderRegistry?: SpecializedOwlRegistry,
    classify?: ClassifyFn,
  ) {
    this.folderRegistry = folderRegistry;
    this.classify = classify;
  }

  async route(message: string, userId: string): Promise<RoutingDecision> {
    const specialists = this.folderRegistry?.listSpecialists() ?? [];

    if (specialists.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logDecision(userId, message, decision);
      return decision;
    }

    // ─── LLM semantic routing ────────────────────────────────────
    if (this.classify) {
      const summaries = specialists.map((s) => ({ name: s.name, role: s.role, expertise: s.expertise }));
      let chosenName: string | null = null;
      try {
        chosenName = await this.classify(message, summaries);
      } catch {
        // fall through to keyword matching
      }

      if (chosenName) {
        const spec = specialists.find((s) => s.name === chosenName);
        if (spec) {
          const decision = { type: "specialist" as const, owl: spec, reason: `LLM routed to: ${chosenName}` };
          log.engine.info(`[SecretaryRouter] LLM → "${chosenName}"`);
          this.logDecision(userId, message, decision);
          return decision;
        }
        log.engine.warn(`[SecretaryRouter] LLM returned unrecognized specialist "${chosenName}" — falling through`);
      }

      if (this.shouldConveneParliament(message)) {
        const decision = { type: "parliament" as const, reason: "Complex query detected" };
        this.logDecision(userId, message, decision);
        return decision;
      }
      const decision = { type: "direct" as const, reason: "LLM classified as no specialist" };
      this.logDecision(userId, message, decision);
      return decision;
    }

    // ─── Keyword fallback ─────────────────────────────────────────
    const messageLower = message.toLowerCase();
    const targets: RoutingTarget[] = specialists.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
    }));

    const matchedTarget = this.findBestMatch(messageLower, targets);
    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(`[SecretaryRouter] Keyword → ${matchedTarget.name} (${confidence.toFixed(2)})`);
        const spec = this.folderRegistry?.get(matchedTarget.name);
        if (spec) {
          const decision = { type: "specialist" as const, owl: spec, reason: `Keyword match: ${matchedTarget.routingRules.slice(0, 3).join(", ")}` };
          this.logDecision(userId, message, decision);
          return decision;
        }
      }
    }

    if (this.shouldConveneParliament(message)) {
      const decision = { type: "parliament" as const, reason: "Complex query detected" };
      this.logDecision(userId, message, decision);
      return decision;
    }

    const decision = { type: "direct" as const, reason: "No specialist match found" };
    this.logDecision(userId, message, decision);
    return decision;
  }

  private findBestMatch(message: string, targets: RoutingTarget[]): RoutingTarget | null {
    let best: RoutingTarget | null = null;
    let bestScore = 0;
    for (const target of targets) {
      const score = this.scoreMatch(message, target);
      if (score > bestScore) { bestScore = score; best = target; }
    }
    return bestScore >= MATCH_SCORE_THRESHOLD ? best : null;
  }

  private scoreMatch(message: string, target: RoutingTarget): number {
    const rules = target.routingRules.map((r) => r.toLowerCase());
    if (rules.length === 0) return 0;
    let matches = 0;
    for (const rule of rules) {
      if (message.includes(rule)) matches++;
    }
    return matches / rules.length;
  }

  private calculateConfidence(message: string, target: RoutingTarget): number {
    const matchScore = this.scoreMatch(message, target);
    return (matchScore * MATCH_WEIGHT) + (0.7 * DNA_WEIGHT);
  }

  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const count = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;
    if (count >= 3) return true;
    if (count >= 2 && message.length > 200) return true;
    return false;
  }

  private logDecision(userId: string, message: string, decision: RoutingDecision): void {
    log.engine.info(`[SecretaryRouter] ${JSON.stringify({
      userId,
      message: message.slice(0, 100),
      type: decision.type,
      target: decision.type === "specialist" ? decision.owl.name : null,
      reason: decision.reason,
    })}`);
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/routing/secretary.test.ts
```
Expected: all PASS.

- [ ] **Step 5: Run full test suite**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/routing/secretary.ts __tests__/routing/secretary.test.ts
git commit -m "refactor: replace SpecializedOwl db type with SpecializedOwlSpec in SecretaryRouter"
```

---

## Task 4: Simplify `RoutingCoordinator` — new constructor + `buildSpecialistPrompt()`

**Files:**
- Modify: `src/gateway/handlers/routing-coordinator.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Rewrite `routing-coordinator.ts`**

Full replacement of `src/gateway/handlers/routing-coordinator.ts`:

```typescript
import type { SpecializedOwlRegistry } from "../../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../../owls/specialized-types.js";
import type { SecretaryRouter } from "../../routing/secretary.js";
import type { GatewayCallbacks, GatewayMessage } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { Session } from "../../memory/store.js";
import { log } from "../../logger.js";

export interface RoutingResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

function buildSpecialistPrompt(spec: SpecializedOwlSpec): string {
  const parts = [
    `You are ${spec.name}, ${spec.role}.`,
    spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
    `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
    spec.permissions.capabilityConstraints.length > 0
      ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.`
      : "",
    spec.additionalPrompt ? `\n\n${spec.additionalPrompt}` : "",
  ];
  return parts.filter(Boolean).join(" ");
}

function activateSpec(
  spec: SpecializedOwlSpec,
  engineCtx: EngineContext,
  callbacks: GatewayCallbacks,
): void {
  const prompt = buildSpecialistPrompt(spec);
  engineCtx.specialistPrompt = prompt;
  engineCtx.owl = { ...engineCtx.owl, specialistPrompt: prompt };
  callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
}

export class RoutingCoordinator {
  constructor(
    private registry: SpecializedOwlRegistry,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private defaultOwlName: string,
  ) {}

  async resolve(
    text: string,
    message: GatewayMessage,
    session: Session,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
  ): Promise<RoutingResult> {
    let activeOwlName = this.defaultOwlName;

    // ─── Check session pin ───────────────────────────────────────
    const pinned = session.metadata.activeOwlName
      ? this.registry.get(session.metadata.activeOwlName)
      : undefined;

    if (pinned) {
      activateSpec(pinned, engineCtx, callbacks);
      log.engine.info(`[RoutingCoordinator] Session pinned to "${pinned.name}"`);
      return { text, activeOwlName: pinned.name, parliamentHandled: false };
    }

    // ─── Explicit @mention ──────────────────────────────────────
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    if (explicitMention) {
      const [, owlName, remainingMessage] = explicitMention;
      const spec = this.registry.get(owlName);
      if (spec) {
        text = remainingMessage?.trim() || "Hello";
        if (spec.type === "coordinator") {
          // Unpin and return to coordinator
          session.metadata.activeOwlName = undefined;
          log.engine.info(`[RoutingCoordinator] @mention → coordinator, pin cleared`);
          return { text, activeOwlName: this.defaultOwlName, parliamentHandled: false };
        }
        activateSpec(spec, engineCtx, callbacks);
        session.metadata.activeOwlName = spec.name;
        activeOwlName = spec.name;
        log.engine.info(`[RoutingCoordinator] @mention → "${spec.name}", session pinned`);
      } else {
        log.engine.warn(`[RoutingCoordinator] @mention "${owlName}" not found in registry`);
      }
    }

    // ─── SecretaryRouter implicit routing ───────────────────────
    if (activeOwlName === this.defaultOwlName && message.userId) {
      const router = this.getSecretaryRouter();
      if (!router) {
        log.engine.warn("[RoutingCoordinator] SecretaryRouter not available — skipping specialist routing");
        return { text, activeOwlName, parliamentHandled: false };
      }

      const decision = await router.route(text, message.userId);

      if (decision.type === "specialist") {
        activateSpec(decision.owl, engineCtx, callbacks);
        session.metadata.activeOwlName = decision.owl.name;
        activeOwlName = decision.owl.name;
        log.engine.info(`[RoutingCoordinator] Routed to "${decision.owl.name}", session pinned`);
      } else if (decision.type === "parliament") {
        log.engine.info(`[RoutingCoordinator] Parliament triggered`);
        return { text, activeOwlName, parliamentHandled: true };
      }
    }

    return { text, activeOwlName, parliamentHandled: false };
  }
}
```

- [ ] **Step 2: Update `RoutingCoordinator` construction in `gateway/core.ts`**

Find the current construction (around line 493):
```typescript
this.routingCoordinator = new RoutingCoordinator(
  ctx.specializedRegistry,
  () => this.secretaryRouter,
  ctx.owlRegistry,
  ctx.owl.persona.name,
);
```

Replace with:
```typescript
this.routingCoordinator = new RoutingCoordinator(
  ctx.specializedRegistry,
  () => this.secretaryRouter,
  ctx.owl.persona.name,
);
```

- [ ] **Step 3: Pass `session` to `resolve()` in `gateway/core.ts`**

Find the current call (around line 1699):
```typescript
routingResult = await this.routingCoordinator.resolve(text, message, engineCtx, callbacks);
```

Replace with:
```typescript
routingResult = await this.routingCoordinator.resolve(text, message, session, engineCtx, callbacks);
```

- [ ] **Step 4: Run TypeScript check**

```bash
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Run full test suite**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/handlers/routing-coordinator.ts src/gateway/core.ts
git commit -m "refactor: simplify RoutingCoordinator — remove owlRegistry param, add session param, add buildSpecialistPrompt"
```

---

## Task 5: Add `activeOwlName` to `Session` metadata

**Files:**
- Modify: `src/memory/store.ts`

- [ ] **Step 1: Add `activeOwlName` field to `Session.metadata`**

In `src/memory/store.ts`, update the `Session` interface:

```typescript
export interface Session {
  id: string;
  messages: ChatMessage[];
  metadata: {
    owlName: string;
    startedAt: number;
    lastUpdatedAt: number;
    title?: string;
    activeOwlName?: string;
  };
}
```

- [ ] **Step 2: Run TypeScript check**

```bash
npx tsc --noEmit
```
Expected: no errors (it's an optional addition).

- [ ] **Step 3: Run full test suite**

```bash
npx vitest run
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/memory/store.ts
git commit -m "feat: add activeOwlName to Session metadata for specialist pinning"
```

---

## Task 6: `SessionStateStore` — persist pin across restarts

**Files:**
- Create: `src/routing/session-state.ts`
- Create: `__tests__/routing/session-state.test.ts`
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/routing-coordinator.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/routing/session-state.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { SessionStateStore } from "../../src/routing/session-state.js";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("SessionStateStore", () => {
  let workspace: string;
  let store: SessionStateStore;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "session-state-test-"));
    store = new SessionStateStore(workspace);
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  it("returns null when no state file exists", async () => {
    const state = await store.load("user123");
    expect(state).toBeNull();
  });

  it("saves and loads session state", async () => {
    await store.save("user123", { activeOwlName: "historyMan", pinnedAt: "2026-01-01T00:00:00Z" });
    const loaded = await store.load("user123");
    expect(loaded).not.toBeNull();
    expect(loaded?.activeOwlName).toBe("historyMan");
  });

  it("clear removes the state file", async () => {
    await store.save("user123", { activeOwlName: "historyMan", pinnedAt: "2026-01-01T00:00:00Z" });
    await store.clear("user123");
    const loaded = await store.load("user123");
    expect(loaded).toBeNull();
  });

  it("saves state for multiple users independently", async () => {
    await store.save("user1", { activeOwlName: "owlA", pinnedAt: "2026-01-01T00:00:00Z" });
    await store.save("user2", { activeOwlName: "owlB", pinnedAt: "2026-01-01T00:00:00Z" });
    const s1 = await store.load("user1");
    const s2 = await store.load("user2");
    expect(s1?.activeOwlName).toBe("owlA");
    expect(s2?.activeOwlName).toBe("owlB");
  });

  it("returns null for corrupt JSON", async () => {
    // Write a corrupt file manually
    const { mkdir, writeFile } = await import("node:fs/promises");
    await mkdir(join(workspace, "sessions"), { recursive: true });
    await writeFile(join(workspace, "sessions", "user123.json"), "not json", "utf-8");
    const loaded = await store.load("user123");
    expect(loaded).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/routing/session-state.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/routing/session-state.ts`**

```typescript
import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";

export interface SessionState {
  activeOwlName: string;
  taskSummary?: string;
  pinnedAt: string;
}

export class SessionStateStore {
  constructor(private readonly workspacePath: string) {}

  private filePath(userId: string): string {
    return join(this.workspacePath, "sessions", `${userId}.json`);
  }

  async load(userId: string): Promise<SessionState | null> {
    const path = this.filePath(userId);
    if (!existsSync(path)) return null;
    try {
      const raw = await readFile(path, "utf-8");
      return JSON.parse(raw) as SessionState;
    } catch {
      return null;
    }
  }

  async save(userId: string, state: SessionState): Promise<void> {
    const path = this.filePath(userId);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, JSON.stringify(state, null, 2), "utf-8");
  }

  async clear(userId: string): Promise<void> {
    const path = this.filePath(userId);
    if (existsSync(path)) {
      await unlink(path);
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/routing/session-state.test.ts
```
Expected: all PASS.

- [ ] **Step 5: Wire `SessionStateStore` into `RoutingCoordinator`**

Add `SessionStateStore` as an optional constructor param to `RoutingCoordinator` and call `load` + `save` inside `resolve()`.

In `src/gateway/handlers/routing-coordinator.ts`, update the import block and class:

```typescript
import type { SessionStateStore } from "../../routing/session-state.js";
```

Update the constructor:

```typescript
export class RoutingCoordinator {
  constructor(
    private registry: SpecializedOwlRegistry,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private defaultOwlName: string,
    private sessionStateStore?: SessionStateStore,
  ) {}
```

In `resolve()`, after the pin check block, add state restoration on first call (before the `@mention` check):

```typescript
// ─── Restore pin from file on first message ──────────────────
if (!session.metadata.activeOwlName && message.userId && this.sessionStateStore) {
  const saved = await this.sessionStateStore.load(message.userId);
  if (saved) {
    session.metadata.activeOwlName = saved.activeOwlName;
    log.engine.info(`[RoutingCoordinator] Restored pin "${saved.activeOwlName}" for user ${message.userId}`);
  }
}
```

After setting `session.metadata.activeOwlName = spec.name` (in both @mention and SecretaryRouter branches), persist the state:

```typescript
if (this.sessionStateStore && message.userId) {
  this.sessionStateStore.save(message.userId, {
    activeOwlName: spec.name,
    pinnedAt: new Date().toISOString(),
  }).catch(() => {});
}
```

When pin is cleared (@noctua), clear the file:

```typescript
if (this.sessionStateStore && message.userId) {
  this.sessionStateStore.clear(message.userId).catch(() => {});
}
```

- [ ] **Step 6: Wire `SessionStateStore` into `gateway/core.ts`**

Add import at top of `src/gateway/core.ts`:

```typescript
import { SessionStateStore } from "../routing/session-state.js";
```

Add private field:

```typescript
private sessionStateStore: SessionStateStore;
```

In the constructor, after `const workspacePath = ctx.cwd ?? process.cwd()`:

```typescript
this.sessionStateStore = new SessionStateStore(workspacePath);
```

Update `RoutingCoordinator` construction:

```typescript
this.routingCoordinator = new RoutingCoordinator(
  ctx.specializedRegistry,
  () => this.secretaryRouter,
  ctx.owl.persona.name,
  this.sessionStateStore,
);
```

- [ ] **Step 7: Run TypeScript check**

```bash
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 8: Run full test suite**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add src/routing/session-state.ts src/gateway/handlers/routing-coordinator.ts src/gateway/core.ts __tests__/routing/session-state.test.ts
git commit -m "feat: add SessionStateStore — persist pinned owl across bot restarts"
```

---

## Task 7: Memory injection — pellets + digest on specialist activation

**Files:**
- Modify: `src/gateway/handlers/routing-coordinator.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Add memory params to `RoutingCoordinator`**

In `src/gateway/handlers/routing-coordinator.ts`, add imports:

```typescript
import type { PelletStore } from "../../pellets/store.js";
import type { ConversationDigestManager } from "../../memory/conversation-digest.js";
```

Update constructor:

```typescript
export class RoutingCoordinator {
  constructor(
    private registry: SpecializedOwlRegistry,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private defaultOwlName: string,
    private sessionStateStore?: SessionStateStore,
    private pelletStore?: PelletStore,
    private digestManager?: ConversationDigestManager,
  ) {}
```

- [ ] **Step 2: Add `injectMemoryContext()` private method**

Add this private method to `RoutingCoordinator`:

```typescript
private async injectMemoryContext(
  owlName: string,
  sessionId: string,
  userMessage: string,
  engineCtx: EngineContext,
): Promise<void> {
  const parts: string[] = [];

  if (this.digestManager) {
    try {
      const digest = await this.digestManager.load(sessionId);
      if (digest?.task) {
        const lines = [`Task: ${digest.task}`];
        if (digest.decisions.length > 0) lines.push(`Decisions: ${digest.decisions.join("; ")}`);
        if (digest.openQuestions.length > 0) lines.push(`Open: ${digest.openQuestions.join("; ")}`);
        parts.push(`## Session Context\n${lines.join("\n")}`);
      }
    } catch { /* non-critical */ }
  }

  if (this.pelletStore) {
    try {
      const pellets = await this.pelletStore.search(userMessage, 3);
      if (pellets.length > 0) {
        const lines = pellets
          .filter((p) => p.owls.includes(owlName) || p.owls.length === 0)
          .map((p) => `- ${p.title}: ${p.content.slice(0, 120)}`)
          .join("\n");
        if (lines) parts.push(`## Related Memory\n${lines}`);
      }
    } catch { /* non-critical */ }
  }

  if (parts.length > 0) {
    const existing = engineCtx.specialistPrompt ?? "";
    engineCtx.specialistPrompt = existing + "\n\n" + parts.join("\n\n");
    engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
  }
}
```

- [ ] **Step 3: Call `injectMemoryContext()` after specialist activation**

In `resolve()`, update `activateSpec()` call sites in the @mention branch and SecretaryRouter branch to also call `injectMemoryContext`. Add after each `session.metadata.activeOwlName = spec.name` line:

```typescript
await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx);
```

Note: also call it when the pin is already set (at the top of `resolve()`, after `activateSpec(pinned, ...)`):

```typescript
await this.injectMemoryContext(pinned.name, message.sessionId, text, engineCtx);
```

- [ ] **Step 4: Wire memory params into `RoutingCoordinator` construction in `gateway/core.ts`**

Update the `RoutingCoordinator` construction call:

```typescript
this.routingCoordinator = new RoutingCoordinator(
  ctx.specializedRegistry,
  () => this.secretaryRouter,
  ctx.owl.persona.name,
  this.sessionStateStore,
  ctx.pelletStore,
  ctx.digestManager,
);
```

- [ ] **Step 5: Run TypeScript check**

```bash
npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```
Expected: all previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/handlers/routing-coordinator.ts src/gateway/core.ts
git commit -m "feat: inject pellet + digest memory context on specialist activation"
```

---

## Task 8: Create sample coordinator owl structure

This task creates example files to show how the owl folder structure works with the new format. No code changes — just documentation files.

**Files:**
- Create: `owls/noctua/specialized_owl.md` (coordinator example)
- Create: `owls/README.md`

- [ ] **Step 1: Create the owls directory and coordinator example**

Create `owls/noctua/specialized_owl.md`:

```markdown
---
name: Noctua
type: coordinator
emoji: 🦉
role: "Chief of Staff and Executive Assistant"
keywords: []
domains:
  - task management
  - scheduling
  - coordination
  - delegation
challengeLevel: medium
verbosity: balanced
tone: direct
---
```

No body is needed — Noctua's full persona lives in the engine as the base system prompt. Specialist owls may include an authored body with domain-specific instructions.

- [ ] **Step 2: Create `owls/README.md`**

```markdown
# Owls

Each subfolder contains one owl definition.

## Coordinator owl (Noctua)

There must be exactly one `type: coordinator` owl. It is the default — it handles all messages and routes to specialists when appropriate.

## Specialist owls

Create a folder `owls/{name}/specialized_owl.md` with `type: specialist`. The body of the markdown file is injected as additional context when this owl is active.

Example: `owls/codeExpert/specialized_owl.md`

```yaml
---
name: CodeExpert
type: specialist
emoji: 💻
role: "Senior Software Engineer"
keywords: [code, bug, function, class, typescript, python, debugging, refactor]
domains: [software engineering, debugging, code review]
challengeLevel: high
verbosity: concise
tone: technical
---

Focus on correctness first, performance second. Always suggest tests.
When reviewing code, identify the root cause — not just the symptom.
```

## Session pinning

When a user's message routes to a specialist, the session is pinned to that specialist. All subsequent messages go directly to the specialist — skipping routing — until the user types `@noctua` to return to the coordinator.
```

- [ ] **Step 3: Commit**

```bash
git add owls/
git commit -m "docs: add coordinator owl example and owls/ README"
```

---

## Phase 2 — Deferred: `OwlRegistry` + `OwlInstance` Deletion

The following changes are **not part of this plan** because they touch 30+ files across the engine, parliament, heartbeat, and CLI layers. Doing them in the same PR risks a large untestable diff.

**Phase 2 work:**
- Delete `src/owls/registry.ts` and all 7 `src/owls/defaults/*/OWL.md` files
- Replace `OwlInstance` with `ActiveOwl` in `EngineContext` and `GatewayContext`
- Rename all `ctx.owl.persona.name` → `ctx.owl.spec.name` across 20+ files
- Remove `owlRegistry` from `GatewayContext` and `EngineContext`
- Update parliament, heartbeat, CLI, and server modules

Phase 2 is a separate plan. Create it after this plan ships and tests are green.
