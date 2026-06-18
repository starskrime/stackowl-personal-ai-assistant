# Provider & Model Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give customers full CRUD control over AI providers via Telegram and TUI — no restarts, no source file edits, with system provider names reserved from customer use.

**Architecture:** Three foundational layers built bottom-up: (1) `deregister()` on the registry enables hot-removal; (2) multi-directory `ModelLoader` enables user-writable provider definitions in `<workspace>/models/`; (3) `ProviderManager` service orchestrates CRUD operations and wires both layers together. UI layers (Telegram config menu + TUI `/provider` command) call ProviderManager — they never touch config or registry directly.

**Tech Stack:** TypeScript (ESM, NodeNext), Vitest, grammY (Telegram), existing `ProviderRegistry`, `ModelLoader`, `StackOwlConfig`, `saveConfig`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `src/providers/registry.ts` | Add `deregister()` method |
| Modify | `src/models/loader.ts` | Multi-directory scan + system name reservation |
| Create | `src/providers/manager.ts` | ProviderManager service (all CRUD logic) |
| Modify | `src/gateway/core.ts` | Add `getProviderRegistry()`, `getProviderManager()` |
| Modify | `src/gateway/adapters/telegram-config/state.ts` | Add `provider_add_name` screen, `pendingName` field |
| Modify | `src/gateway/adapters/telegram-config/menu.ts` | Accept ProviderManager; use it in add/remove/edit |
| Modify | `src/gateway/adapters/telegram.ts` | Wire ProviderManager into TelegramConfigMenu ctor |
| Create | `src/cli/v2/commands/handlers/provider.ts` | TUI `/provider` command handlers |
| Modify | `src/cli/v2/commands/registry.ts` | Register `/provider` command |
| Create | `__tests__/providers/registry-deregister.test.ts` | deregister tests |
| Create | `__tests__/models/loader-multdir.test.ts` | Multi-dir loader tests |
| Create | `__tests__/providers/manager.test.ts` | ProviderManager tests |
| Create | `__tests__/cli/v2/commands/provider.test.ts` | TUI provider handler tests |

---

## Task 1: Add `deregister()` to ProviderRegistry

**Files:**
- Modify: `src/providers/registry.ts:363-375` (after `listProviders()`)
- Create: `__tests__/providers/registry-deregister.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/providers/registry-deregister.test.ts
import { describe, it, expect, vi } from "vitest";
import { ProviderRegistry } from "../../src/providers/registry.js";
import type { ModelProvider } from "../../src/providers/base.js";

function makeProvider(): ModelProvider {
  return {
    name: "test",
    chat: vi.fn(),
    stream: vi.fn(),
    healthCheck: vi.fn().mockResolvedValue(true),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider;
}

describe("ProviderRegistry.deregister", () => {
  it("removes the provider so get() throws after deregistration", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-a", makeProvider());
    expect(() => reg.get("prov-a")).not.toThrow();

    reg.deregister("prov-a");

    expect(() => reg.get("prov-a")).toThrow(/prov-a.*not found/i);
  });

  it("removes circuit breaker entry", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-b", makeProvider());
    reg.deregister("prov-b");

    // isProviderOpen returns false (no breaker) — not true
    expect(reg.isProviderOpen("prov-b")).toBe(false);
  });

  it("clears role assignments for the deregistered provider", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-c", makeProvider());
    reg.assignRole("synthesizer", "prov-c");
    reg.deregister("prov-c");

    // byRole falls back to default — with no default set, it throws
    expect(() => reg.byRole("synthesizer")).toThrow();
  });

  it("clears defaultProviderName when deregistering the default", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-d", makeProvider());
    reg._registerForTest("prov-e", makeProvider());
    // Manually set default (setDefault requires registry.register, bypass via context)
    (reg as any).defaultProviderName = "prov-d";
    reg.deregister("prov-d");

    expect(reg.getDefaultName()).toBeNull();
  });

  it("is a no-op for unknown provider names", () => {
    const reg = new ProviderRegistry();
    expect(() => reg.deregister("does-not-exist")).not.toThrow();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npx vitest run __tests__/providers/registry-deregister.test.ts
```

Expected: FAIL — `reg.deregister is not a function`

- [ ] **Step 3: Add `deregister()` to `src/providers/registry.ts`**

Add this method after `listProviders()` (line ~365), before `healthCheckAll()`:

```typescript
/**
 * Deregister a provider and clean up all associated state.
 * Safe to call for an unknown name (no-op).
 */
deregister(name: string): void {
  log.engine.debug("provider-registry.deregister: entry", { name });
  this.providers.delete(name);
  this.breakers.delete(name);
  for (const [role, assigned] of this.roles) {
    if (assigned === name) this.roles.delete(role);
  }
  if (this.defaultProviderName === name) {
    this.defaultProviderName = null;
  }
  log.engine.debug("provider-registry.deregister: exit", { name });
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npx vitest run __tests__/providers/registry-deregister.test.ts
```

Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add src/providers/registry.ts __tests__/providers/registry-deregister.test.ts
git commit -m "feat(registry): add deregister() for hot-removal of providers"
```

---

## Task 2: Multi-directory ModelLoader with system name reservation

**Files:**
- Modify: `src/models/loader.ts` (full rewrite of class + singleton)
- Create: `__tests__/models/loader-multidir.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/models/loader-multidir.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ModelLoader, resetModelLoader } from "../../src/models/loader.js";

function writeTempModel(dir: string, name: string, content: string): void {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, name), content, "utf-8");
}

describe("ModelLoader — multi-directory", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = join(tmpdir(), `loader-test-${Date.now()}`);
    resetModelLoader();
  });

  afterEach(() => {
    try { rmSync(tmpDir, { recursive: true }); } catch { /* ok */ }
    resetModelLoader();
  });

  it("loads models from a user directory in addition to system dir", () => {
    writeTempModel(tmpDir, "my-custom", "compatible: openai\nurl: \"http://localhost:9999/v1\"\ndefaultModel: \"custom-model\"\navailableModels: [\"custom-model\"]");
    const loader = new ModelLoader([tmpDir]);
    const def = loader.get("my-custom");
    expect(def).not.toBeNull();
    expect(def?.compatible).toBe("openai");
    expect(def?.url).toBe("http://localhost:9999/v1");
  });

  it("system names are reserved — user dir file with system name is ignored", () => {
    // "anthropic" is a system name
    writeTempModel(tmpDir, "anthropic", "compatible: anthropic\nurl: \"http://fake.com/v1\"\ndefaultModel: \"fake-model\"\navailableModels: [\"fake-model\"]");
    const loader = new ModelLoader([tmpDir]);
    // System anthropic definition must still be the real one
    const def = loader.get("anthropic");
    expect(def?.url).toContain("api.anthropic.com");
  });

  it("isSystemName() returns true for built-in providers", () => {
    const loader = new ModelLoader();
    expect(loader.isSystemName("anthropic")).toBe(true);
    expect(loader.isSystemName("openai")).toBe(true);
    expect(loader.isSystemName("ollama")).toBe(true);
  });

  it("isSystemName() returns false for user-added providers", () => {
    writeTempModel(tmpDir, "my-llm", "compatible: openai\nurl: \"http://localhost:9999/v1\"\ndefaultModel: \"llm\"\navailableModels: [\"llm\"]");
    const loader = new ModelLoader([tmpDir]);
    expect(loader.isSystemName("my-llm")).toBe(false);
  });

  it("user directory is non-blocking when it does not exist", () => {
    const nonExistentDir = join(tmpDir, "no-such-dir");
    expect(() => new ModelLoader([nonExistentDir])).not.toThrow();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npx vitest run __tests__/models/loader-multidir.test.ts
```

Expected: FAIL — `resetModelLoader is not a function`, `isSystemName is not a function`, system name reservation not implemented.

- [ ] **Step 3: Rewrite `src/models/loader.ts`**

Replace the entire file with:

```typescript
/**
 * StackOwl — Model Definition Loader
 *
 * Scans src/models/ for built-in provider definitions, then any additional
 * user directories (e.g. <workspace>/models/). System names are reserved —
 * user files that conflict with a system name are silently skipped.
 *
 * File format: simple key:value lines (no extension).
 *   compatible: anthropic
 *   url: "https://api.anthropic.com/v1"
 *   availableModels: ["claude-sonnet-4-6"]
 *   defaultModel: "claude-sonnet-4-6"
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export type ProtocolId = "openai" | "anthropic" | "gemini" | "grok";

export interface ModelDefinition {
  name: string;
  compatible: ProtocolId;
  availableModels: string[];
  defaultModel: string;
  url: string;
  requiresApiKey?: boolean;
}

// ─── Parser ─────────────────────────────────────────────────────

function parseModelFile(name: string, content: string): ModelDefinition | null {
  const result: Record<string, unknown> = { name };

  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;

    const colonIdx = line.indexOf(":");
    if (colonIdx < 0) continue;

    const key = line.slice(0, colonIdx).trim();
    const rawVal = line.slice(colonIdx + 1).trim();
    if (!key || !rawVal) continue;

    try {
      result[key] = JSON.parse(rawVal);
    } catch {
      result[key] = rawVal;
    }
  }

  if (!result["compatible"] || !result["url"]) return null;

  if (!result["availableModels"]) result["availableModels"] = [];
  if (!result["defaultModel"]) {
    const models = result["availableModels"] as string[];
    result["defaultModel"] = models[0] ?? "";
  }

  return result as unknown as ModelDefinition;
}

// ─── Loader ─────────────────────────────────────────────────────

export class ModelLoader {
  private defs = new Map<string, ModelDefinition>();
  private systemNames = new Set<string>();

  /**
   * @param extraDirs  Additional directories to scan after the built-in
   *                   src/models/ directory. Files whose names match a system
   *                   name are silently skipped (system names are reserved).
   */
  constructor(extraDirs?: string[]) {
    const systemDir = join(dirname(fileURLToPath(import.meta.url)));
    this._loadDir(systemDir);
    // Capture all system names before loading user dirs
    for (const name of this.defs.keys()) {
      this.systemNames.add(name);
    }
    if (extraDirs) {
      for (const dir of extraDirs) {
        this._loadDir(dir, /* skipSystemNames= */ true);
      }
    }
  }

  private _loadDir(dir: string, skipSystemNames = false): void {
    try {
      const entries = readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        if (/\.(ts|js|map|json)$/.test(entry.name)) continue;
        if (skipSystemNames && this.systemNames.has(entry.name)) continue;
        try {
          const content = readFileSync(join(dir, entry.name), "utf-8");
          const def = parseModelFile(entry.name, content);
          if (def) this.defs.set(entry.name, def);
        } catch {
          // skip unreadable files
        }
      }
    } catch {
      // directory may not exist
    }
  }

  get(name: string): ModelDefinition | null {
    return this.defs.get(name) ?? null;
  }

  getAll(): ModelDefinition[] {
    return Array.from(this.defs.values());
  }

  has(name: string): boolean {
    return this.defs.has(name);
  }

  /** Returns true if `name` is a built-in system provider (reserved). */
  isSystemName(name: string): boolean {
    return this.systemNames.has(name);
  }
}

// ─── Singleton ───────────────────────────────────────────────────

let _instance: ModelLoader | null = null;

/** Initialize (or reinitialize) the singleton with an optional user models directory. */
export function initModelLoader(workspaceModelsDir?: string): ModelLoader {
  _instance = new ModelLoader(workspaceModelsDir ? [workspaceModelsDir] : undefined);
  return _instance;
}

/** Returns the singleton, creating a default one (system models only) if not yet initialized. */
export function getModelLoader(): ModelLoader {
  if (!_instance) _instance = new ModelLoader();
  return _instance;
}

/** Reset the singleton (for testing). */
export function resetModelLoader(): void {
  _instance = null;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npx vitest run __tests__/models/loader-multidir.test.ts
```

Expected: PASS (5/5)

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
npx vitest run
```

Expected: all previously passing tests still pass (ModelLoader singleton behavior unchanged for callers that use `getModelLoader()` with no args).

- [ ] **Step 6: Commit**

```bash
git add src/models/loader.ts __tests__/models/loader-multidir.test.ts
git commit -m "feat(loader): multi-directory ModelLoader with system name reservation"
```

---

## Task 3: ProviderManager service

**Files:**
- Create: `src/providers/manager.ts`
- Create: `__tests__/providers/manager.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/providers/manager.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdirSync, rmSync } from "node:fs";
import { ProviderManager } from "../../src/providers/manager.js";
import { ProviderRegistry } from "../../src/providers/registry.js";
import type { StackOwlConfig } from "../../src/config/loader.js";
import type { ModelProvider } from "../../src/providers/base.js";
import { initModelLoader, resetModelLoader } from "../../src/models/loader.js";

function makeProvider(name = "test"): ModelProvider {
  return {
    name,
    chat: vi.fn(),
    stream: vi.fn(),
    healthCheck: vi.fn().mockResolvedValue(true),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider;
}

function makeConfig(overrides: Partial<StackOwlConfig> = {}): StackOwlConfig {
  return {
    providers: { anthropic: { apiKey: "sk-ant-existing" } },
    defaultProvider: "anthropic",
    defaultModel: "claude-sonnet-4-6",
    workspace: "./workspace",
    gateway: { port: 3077, host: "127.0.0.1", outputMode: "normal" },
    ...overrides,
  } as StackOwlConfig;
}

describe("ProviderManager", () => {
  let tmpDir: string;
  let registry: ProviderRegistry;
  let config: StackOwlConfig;
  let saveFn: ReturnType<typeof vi.fn>;
  let manager: ProviderManager;

  beforeEach(() => {
    tmpDir = join(tmpdir(), `pm-test-${Date.now()}`);
    mkdirSync(tmpDir, { recursive: true });
    resetModelLoader();
    initModelLoader(); // system models only

    registry = new ProviderRegistry();
    registry._registerForTest("anthropic", makeProvider("anthropic"));
    (registry as any).defaultProviderName = "anthropic";

    config = makeConfig();
    saveFn = vi.fn().mockResolvedValue(undefined);
    manager = new ProviderManager(registry, config, tmpDir, saveFn);
  });

  afterEach(() => {
    try { rmSync(tmpDir, { recursive: true }); } catch { /* ok */ }
    resetModelLoader();
  });

  // ── addProvider ──────────────────────────────────────────────────

  it("addProvider: throws on reserved system name", async () => {
    await expect(
      manager.addProvider({ name: "anthropic", profile: "anthropic", apiKey: "sk-ant-123" }),
    ).rejects.toThrow(/reserved/i);
  });

  it("addProvider: throws when name already exists in config", async () => {
    await expect(
      manager.addProvider({ name: "anthropic", profile: "anthropic", apiKey: "sk-ant-123" }),
    ).rejects.toThrow();
  });

  it("addProvider: writes config entry and calls saveFn", async () => {
    await manager.addProvider({ name: "my-openai", profile: "openai", apiKey: "sk-123", activeModel: "gpt-5" });
    expect(config.providers["my-openai"]).toMatchObject({ profile: "openai", apiKey: "sk-123", activeModel: "gpt-5" });
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("addProvider: throws on invalid name characters", async () => {
    await expect(
      manager.addProvider({ name: "my_provider!", profile: "openai", apiKey: "sk-123" }),
    ).rejects.toThrow(/invalid.*name/i);
  });

  // ── editProvider ─────────────────────────────────────────────────

  it("editProvider: updates apiKey in config and saves", async () => {
    await manager.editProvider("anthropic", { apiKey: "sk-ant-new-key" });
    expect(config.providers["anthropic"]?.apiKey).toBe("sk-ant-new-key");
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("editProvider: throws for unknown provider name", async () => {
    await expect(manager.editProvider("no-such", { apiKey: "x" })).rejects.toThrow(/not found/i);
  });

  // ── deleteProvider ───────────────────────────────────────────────

  it("deleteProvider: throws when trying to delete the default provider", async () => {
    await expect(manager.deleteProvider("anthropic")).rejects.toThrow(/default/i);
  });

  it("deleteProvider: removes provider from config and saves", async () => {
    config.providers["my-openai"] = { profile: "openai", apiKey: "sk-123" };
    registry._registerForTest("my-openai", makeProvider("my-openai"));
    await manager.deleteProvider("my-openai");
    expect(config.providers["my-openai"]).toBeUndefined();
    expect(saveFn).toHaveBeenCalledOnce();
  });

  it("deleteProvider: calls registry.deregister", async () => {
    config.providers["my-openai"] = { profile: "openai", apiKey: "sk-123" };
    registry._registerForTest("my-openai", makeProvider("my-openai"));
    await manager.deleteProvider("my-openai");
    expect(() => registry.get("my-openai")).toThrow();
  });

  // ── listProviders ────────────────────────────────────────────────

  it("listProviders: returns all config providers with status", () => {
    const statuses = manager.listProviders();
    expect(statuses).toHaveLength(1);
    expect(statuses[0]).toMatchObject({
      name: "anthropic",
      isDefault: true,
    });
  });

  // ── testProvider ─────────────────────────────────────────────────

  it("testProvider: returns ok:true when healthCheck resolves true", async () => {
    const result = await manager.testProvider("anthropic");
    expect(result.ok).toBe(true);
    expect(result.latencyMs).toBeGreaterThanOrEqual(0);
  });

  it("testProvider: returns ok:false with error message when healthCheck throws", async () => {
    const failProvider = makeProvider("fail");
    (failProvider.healthCheck as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("connection refused"));
    registry._registerForTest("fail-prov", failProvider);
    config.providers["fail-prov"] = { profile: "openai" };
    const result = await manager.testProvider("fail-prov");
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/connection refused/i);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npx vitest run __tests__/providers/manager.test.ts
```

Expected: FAIL — `Cannot find module '../../src/providers/manager.js'`

- [ ] **Step 3: Create `src/providers/manager.ts`**

```typescript
/**
 * StackOwl — Provider Manager
 *
 * Single service for all provider CRUD operations.
 * Owns the coordination between ModelLoader, ProviderRegistry, config,
 * and workspace model files.
 *
 * Rules:
 *  - System provider names (from src/models/) are reserved. Customers
 *    cannot create providers with those names.
 *  - Standard providers (using a system protocol) need only a config entry.
 *  - Custom providers (new protocol endpoint) also write a workspace model file.
 *  - All mutations hot-register/deregister without requiring a restart.
 */

import { writeFile, unlink, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { log } from "../logger.js";
import { getModelLoader, initModelLoader, resetModelLoader } from "../models/loader.js";
import type { ProtocolId } from "../models/loader.js";
import type { ProviderRegistry } from "./registry.js";
import type { StackOwlConfig, ProviderConfigEntry } from "../config/loader.js";

// ─── Input / Output types ────────────────────────────────────────

export interface CustomProviderConfig {
  compatible: ProtocolId;
  url: string;
  availableModels: string[];
  defaultModel: string;
}

export interface AddProviderInput {
  /** Unique customer-chosen name (alphanumeric + hyphens) */
  name: string;
  /** System model file to use as the protocol (e.g. "anthropic", "openai") */
  profile: string;
  apiKey?: string;
  activeModel?: string;
  baseUrl?: string;
  /** Provide this only when adding a fully custom provider (creates workspace model file) */
  customConfig?: CustomProviderConfig;
}

export interface ProviderUpdates {
  apiKey?: string;
  activeModel?: string;
  baseUrl?: string;
}

export interface ProviderStatus {
  name: string;
  profile: string;
  activeModel: string;
  isDefault: boolean;
  health: "CLOSED" | "OPEN" | "HALF_OPEN" | "unconfigured";
  source: "system" | "custom";
}

export interface TestResult {
  ok: boolean;
  latencyMs: number;
  error?: string;
}

// ─── Validation ──────────────────────────────────────────────────

const NAME_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

// ─── Manager ─────────────────────────────────────────────────────

export class ProviderManager {
  constructor(
    private registry: ProviderRegistry,
    private config: StackOwlConfig,
    private workspacePath: string,
    private saveFn: (config: StackOwlConfig) => Promise<void>,
  ) {}

  get workspaceModelsDir(): string {
    return join(this.workspacePath, "models");
  }

  // ─── Create ──────────────────────────────────────────────────

  async addProvider(input: AddProviderInput): Promise<void> {
    log.engine.debug("provider-manager.addProvider: entry", { name: input.name });

    if (!NAME_RE.test(input.name)) {
      throw new Error(
        `Invalid provider name "${input.name}". Use lowercase alphanumeric characters and hyphens only (e.g. "my-openai").`,
      );
    }

    const loader = getModelLoader();

    if (loader.isSystemName(input.name)) {
      throw new Error(
        `Name "${input.name}" is reserved. Choose a different name (e.g., "${input.name}-prod").`,
      );
    }

    if (this.config.providers[input.name]) {
      throw new Error(`Provider "${input.name}" already exists.`);
    }

    // Write workspace model file for custom providers
    if (input.customConfig) {
      await mkdir(this.workspaceModelsDir, { recursive: true });
      await this._writeModelFile(input.name, input.customConfig);
      // Reload loader to pick up new file
      resetModelLoader();
      initModelLoader(this.workspaceModelsDir);
    }

    // Write config entry
    const entry: ProviderConfigEntry = {
      profile: input.profile,
      ...(input.apiKey !== undefined && { apiKey: input.apiKey }),
      ...(input.activeModel !== undefined && { activeModel: input.activeModel }),
      ...(input.baseUrl !== undefined && { baseUrl: input.baseUrl }),
    };
    this.config.providers[input.name] = entry;
    await this.saveFn(this.config);

    // Hot-register
    try {
      this.registry.register({ name: input.name, ...entry });
      log.engine.debug("provider-manager.addProvider: registered", { name: input.name });
    } catch (err) {
      log.engine.warn("provider-manager.addProvider: hot-register failed (saved to config)", err as Error, { name: input.name });
    }

    log.engine.debug("provider-manager.addProvider: exit", { name: input.name });
  }

  // ─── Update ──────────────────────────────────────────────────

  async editProvider(name: string, updates: ProviderUpdates): Promise<void> {
    log.engine.debug("provider-manager.editProvider: entry", { name, updates: Object.keys(updates) });

    const entry = this.config.providers[name];
    if (!entry) throw new Error(`Provider "${name}" not found.`);

    if (updates.apiKey !== undefined) entry.apiKey = updates.apiKey;
    if (updates.activeModel !== undefined) entry.activeModel = updates.activeModel;
    if (updates.baseUrl !== undefined) entry.baseUrl = updates.baseUrl;

    await this.saveFn(this.config);

    // Re-register with updated config
    this.registry.deregister(name);
    try {
      this.registry.register({ name, ...entry });
    } catch (err) {
      log.engine.warn("provider-manager.editProvider: re-register failed", err as Error, { name });
    }

    log.engine.debug("provider-manager.editProvider: exit", { name });
  }

  // ─── Delete ──────────────────────────────────────────────────

  async deleteProvider(name: string): Promise<void> {
    log.engine.debug("provider-manager.deleteProvider: entry", { name });

    if (this.config.defaultProvider === name) {
      throw new Error(
        `Cannot delete "${name}" — it is the current default provider. Set another provider as default first.`,
      );
    }

    // Deregister from runtime
    this.registry.deregister(name);

    // Remove from config
    delete this.config.providers[name];
    await this.saveFn(this.config);

    // Remove workspace model file if it exists (custom providers only)
    const modelFilePath = join(this.workspaceModelsDir, name);
    try {
      await unlink(modelFilePath);
      log.engine.debug("provider-manager.deleteProvider: removed workspace model file", { name });
    } catch {
      // file doesn't exist for standard providers — that's fine
    }

    log.engine.debug("provider-manager.deleteProvider: exit", { name });
  }

  // ─── Read ────────────────────────────────────────────────────

  listProviders(): ProviderStatus[] {
    const loader = getModelLoader();
    const registeredNames = new Set(this.registry.listProviders());

    return Object.entries(this.config.providers).map(([name, entry]) => {
      const profile = entry.profile ?? name;
      const modelDef = loader.get(profile);
      const activeModel =
        entry.activeModel ?? entry.defaultModel ?? modelDef?.defaultModel ?? "unknown";

      let health: ProviderStatus["health"] = "unconfigured";
      if (registeredNames.has(name)) {
        const open = this.registry.isProviderOpen(name);
        const breaker = (this.registry as any).breakers?.get(name);
        if (open) {
          health = "OPEN";
        } else if (breaker?.getState() === "HALF_OPEN") {
          health = "HALF_OPEN";
        } else {
          health = "CLOSED";
        }
      }

      return {
        name,
        profile,
        activeModel,
        isDefault: this.config.defaultProvider === name,
        health,
        source: loader.isSystemName(profile) ? "system" : "custom",
      } satisfies ProviderStatus;
    });
  }

  // ─── Test ────────────────────────────────────────────────────

  async testProvider(name: string): Promise<TestResult> {
    log.engine.debug("provider-manager.testProvider: entry", { name });
    const start = Date.now();
    try {
      const provider = this.registry.get(name);
      const ok = await provider.healthCheck();
      const latencyMs = Date.now() - start;
      log.engine.debug("provider-manager.testProvider: exit", { name, ok, latencyMs });
      return { ok, latencyMs };
    } catch (err) {
      const latencyMs = Date.now() - start;
      const error = err instanceof Error ? err.message : String(err);
      log.engine.warn("provider-manager.testProvider: failed", err as Error, { name });
      return { ok: false, latencyMs, error };
    }
  }

  // ─── Private ─────────────────────────────────────────────────

  private async _writeModelFile(name: string, cfg: CustomProviderConfig): Promise<void> {
    const lines = [
      `compatible: ${cfg.compatible}`,
      `url: "${cfg.url}"`,
      `availableModels: ${JSON.stringify(cfg.availableModels)}`,
      `defaultModel: "${cfg.defaultModel}"`,
    ];
    await writeFile(join(this.workspaceModelsDir, name), lines.join("\n"), "utf-8");
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npx vitest run __tests__/providers/manager.test.ts
```

Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/providers/manager.ts __tests__/providers/manager.test.ts
git commit -m "feat(providers): ProviderManager service for full provider CRUD"
```

---

## Task 4: Wire ProviderManager into OwlGateway

**Files:**
- Modify: `src/gateway/core.ts` (add `getProviderRegistry()` and `getProviderManager()`)
- Modify: `src/index.ts` (call `initModelLoader` with workspace models dir at boot)
- Modify: `src/gateway/adapters/telegram.ts` (pass providerManager to TelegramConfigMenu)

- [ ] **Step 1: Add `getProviderRegistry()` and `getProviderManager()` to `OwlGateway`**

In `src/gateway/core.ts`, find the existing public getters section (around line 3244). Add after `getWorkspacePath()`:

```typescript
getProviderRegistry(): import("../providers/registry.js").ProviderRegistry | undefined {
  return this.ctx.providerRegistry;
}

getProviderManager(): import("../providers/manager.js").ProviderManager {
  if (!this._providerManager) {
    const registry = this.ctx.providerRegistry;
    if (!registry) throw new Error("[OwlGateway] ProviderRegistry not initialized.");
    const workspacePath = this.ctx.cwd ?? process.cwd();
    const { ProviderManager } = require("../providers/manager.js") as typeof import("../providers/manager.js");
    const { saveConfig } = require("../config/loader.js") as typeof import("../config/loader.js");
    this._providerManager = new ProviderManager(
      registry,
      this.ctx.config,
      workspacePath,
      (cfg) => saveConfig(workspacePath, cfg),
    );
  }
  return this._providerManager;
}
private _providerManager?: import("../providers/manager.js").ProviderManager;
```

**Note:** To use ESM-safe imports instead of `require()`, add the import at the top of `core.ts`:

```typescript
import { ProviderManager } from "../providers/manager.js";
import { saveConfig } from "../config/loader.js";
```

Then replace the lazy init body:

```typescript
getProviderManager(): ProviderManager {
  if (!this._providerManager) {
    const registry = this.ctx.providerRegistry;
    if (!registry) throw new Error("[OwlGateway] ProviderRegistry not initialized.");
    const workspacePath = this.ctx.cwd ?? process.cwd();
    this._providerManager = new ProviderManager(
      registry,
      this.ctx.config,
      workspacePath,
      (cfg) => saveConfig(workspacePath, cfg),
    );
  }
  return this._providerManager;
}
private _providerManager?: ProviderManager;
```

- [ ] **Step 2: Initialize ModelLoader with workspace models dir at boot**

In `src/index.ts`, find where `providerRegistry` is created (around line 372). Before the `ProviderRegistry` construction, add:

```typescript
import { initModelLoader } from "./models/loader.js";

// Initialize model loader with workspace models dir so customer providers are visible
const workspaceModelsDir = join(basePath, "workspace", "models");
initModelLoader(workspaceModelsDir);
```

Where `basePath` is the directory passed to `loadConfig()`. Check the actual variable name in the file.

- [ ] **Step 3: Wire ProviderManager into TelegramConfigMenu**

In `src/gateway/adapters/telegram.ts`, find the `TelegramConfigMenu` constructor call (around line 97). Update it to also pass a `providerManager` argument:

```typescript
this.configMenu = new TelegramConfigMenu(
  () => gateway.getConfig(),
  async (cfg) => {
    const basePath = gateway.getWorkspacePath();
    await saveConfig(basePath, cfg);
  },
  (gateway.getConfig() as any).gateway?.port ?? 3077,
  {
    get(name: string) {
      try { return (gateway as any).ctx?.providerRegistry?.get(name) ?? gateway.getProvider(); }
      catch (err) { log.telegram.warn("providerRegistry.get failed, using default provider", err, { name }); return gateway.getProvider(); }
    },
    listProviders() {
      try { return (gateway as any).ctx?.providerRegistry?.listProviders() ?? [gwConfig.defaultProvider]; }
      catch (err) { log.telegram.warn("providerRegistry.listProviders failed, using default", err); return [gwConfig.defaultProvider]; }
    },
  },
  // NEW: pass providerManager
  gateway.getProviderManager(),
);
```

- [ ] **Step 4: Build TypeScript to check for errors**

```bash
npm run build 2>&1 | head -40
```

Expected: no new errors. Fix any type errors before continuing.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts src/index.ts src/gateway/adapters/telegram.ts
git commit -m "feat(gateway): expose ProviderManager via getProviderManager(), init ModelLoader with workspace dir"
```

---

## Task 5: Update TelegramConfigMenu to use ProviderManager

**Files:**
- Modify: `src/gateway/adapters/telegram-config/state.ts` (add `provider_add_name` screen, `pendingName` field)
- Modify: `src/gateway/adapters/telegram-config/menu.ts` (accept providerManager, use it in add/remove, add name step)
- Modify: `src/gateway/adapters/telegram-config/screens.ts` (add `renderAddProviderName` screen)

**Context:** The existing `TelegramConfigMenu` in `menu.ts` has provider add/remove flows, but they: (1) use `providerType` as the key (no custom name), (2) call `saveConfigFn()` directly without hot-registering, (3) don't check system name reservation, (4) don't call `registry.deregister()` on remove. This task fixes all four.

- [ ] **Step 1: Add `provider_add_name` to state.ts**

In `src/gateway/adapters/telegram-config/state.ts`, add `"provider_add_name"` to the `MenuScreen` union type (after `"provider_add_type"`):

```typescript
export type MenuScreen =
  | "main"
  | "providers"
  | "provider_detail"
  | "provider_add_name"    // ← new: collect unique name before protocol
  | "provider_add_type"
  | "provider_add_url"
  | "provider_add_key"
  | "provider_model_pick"
  | "model_roles"
  | "model_role_prov_pick"
  | "model_role_model_pick"
  | "health_check";
```

Add `pendingName?: string` and `"name"` as a `PendingInput.field` option to `MenuState`:

```typescript
export interface PendingInput {
  field: "baseUrl" | "apiKey" | "modelSearch" | "name";
  contextKey: string;
}

export interface MenuState {
  // ... existing fields ...
  /** Customer-chosen provider name from the add flow */
  pendingName?: string;
  // ... rest unchanged ...
}
```

- [ ] **Step 2: Add `renderAddProviderName()` screen to screens.ts**

In `src/gateway/adapters/telegram-config/screens.ts`, add after `renderAddProviderType`:

```typescript
/** Screen: prompt user to enter a unique name for the new provider */
export function renderAddProviderName(): ScreenContent {
  const text =
    `➕ <b>Add Provider — Step 1 of 3</b>\n\n` +
    `Choose a unique name for this provider.\n` +
    `Use lowercase letters, numbers, and hyphens.\n\n` +
    `Examples: <code>my-openai</code>, <code>anthropic-prod</code>, <code>llama-corp</code>`;

  const keyboard = new InlineKeyboard()
    .text("← Cancel", "cfg:~");

  return { text, keyboard };
}
```

Also add the `"name"` branch to `PendingInput.field` in the jsdoc/type usages in screens.ts if needed (or leave as is since it's in state.ts).

- [ ] **Step 3: Update `TelegramConfigMenu` constructor to accept `ProviderManager`**

In `src/gateway/adapters/telegram-config/menu.ts`, add the import at the top:

```typescript
import type { ProviderManager } from "../../../providers/manager.js";
```

Update the constructor to accept an optional `providerManager`:

```typescript
constructor(
  private getConfig: () => StackOwlConfig,
  private saveConfigFn: (config: StackOwlConfig) => Promise<void>,
  private gatewayPort: number,
  private providerRegistry: {
    get(name: string): { healthCheck(): Promise<boolean>; listModels(): Promise<string[]> };
    listProviders(): string[];
  },
  private providerManager?: ProviderManager,  // ← new
) {}
```

- [ ] **Step 4: Add name input step at the start of the add flow**

In `menu.ts`, update the `"pa"` case in `route()` to start with the name step instead of jumping straight to protocol picker. Replace the existing `if (cmd === "pa")` block:

```typescript
if (cmd === "pa") {
  // Start add flow: collect a unique name first
  this.stateManager.navigate(state.userId, "provider_add_name");
  state.pendingEntry = undefined;
  state.pendingName = undefined;
  state.pendingInput = { field: "name", contextKey: "" };
  await this.editScreen(ctx, state, renderAddProviderName());
  return;
}
```

Add `renderAddProviderName` to the imports from `./screens.js`.

- [ ] **Step 5: Handle the name text input in `handleTextInput()`**

In `handleTextInput()`, add handling for `field === "name"` before the existing `field === "apiKey"` block:

```typescript
if (field === "name") {
  await this.applyProviderName(ctx, state, text.trim());
  return true;
}
```

Add the `applyProviderName` private method:

```typescript
private async applyProviderName(
  ctx: Context,
  state: MenuState,
  name: string,
): Promise<void> {
  // Validate format
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(name)) {
    await this.editScreen(ctx, state, renderError(
      `Invalid name "<b>${name}</b>". Use lowercase letters, numbers, and hyphens only.\n` +
      `Examples: <code>my-openai</code>, <code>anthropic-prod</code>`,
    ));
    state.pendingInput = { field: "name", contextKey: "" };
    return;
  }

  // Check system name reservation
  const config = this.getConfig();
  if (this.providerManager && this.providerManager["_isReservedOrDuplicate"]?.(name, config)) {
    await this.editScreen(ctx, state, renderError(
      `Name "<b>${name}</b>" is reserved or already in use. Choose a different name.`,
    ));
    state.pendingInput = { field: "name", contextKey: "" };
    return;
  }

  // Simple in-menu check for duplicates (fallback when no manager)
  if (config.providers[name]) {
    await this.editScreen(ctx, state, renderError(
      `Provider "<b>${name}</b>" already exists. Choose a different name.`,
    ));
    state.pendingInput = { field: "name", contextKey: "" };
    return;
  }

  state.pendingName = name;
  // Move to protocol type selection
  this.stateManager.navigate(state.userId, "provider_add_type");
  await this.editScreen(ctx, state, renderAddProviderType());
}
```

Also add a `_isReservedOrDuplicate` helper to `ProviderManager` in `src/providers/manager.ts`:

```typescript
/** Used by the Telegram menu for inline validation. */
isReservedOrDuplicate(name: string, config: StackOwlConfig): boolean {
  return getModelLoader().isSystemName(name) || !!config.providers[name];
}
```

And update `applyProviderName` to call it without the `["..."]` hack:

```typescript
if (this.providerManager && this.providerManager.isReservedOrDuplicate(name, config)) {
  // ... error ...
}
```

- [ ] **Step 6: Update `finalizeProviderAdd` to use ProviderManager**

Replace the existing `finalizeProviderAdd` method body:

```typescript
private async finalizeProviderAdd(
  ctx: Context,
  state: MenuState,
  providerType: string,
  apiKey: string | undefined,
): Promise<void> {
  const entry  = state.pendingEntry ?? { providerType };
  const name   = state.pendingName ?? providerType; // fallback to type for legacy flows
  const config = this.getConfig();

  try {
    if (this.providerManager) {
      await this.providerManager.addProvider({
        name,
        profile: providerType,
        apiKey:       apiKey || undefined,
        activeModel:  entry.defaultModel,
        baseUrl:      entry.baseUrl || undefined,
      });
    } else {
      // Fallback: direct config mutation (no hot-registration)
      config.providers[name] = {
        profile:      providerType,
        baseUrl:      entry.baseUrl || undefined,
        apiKey:       apiKey || undefined,
        defaultModel: entry.defaultModel,
      };
      await this.saveConfigFn(config);
    }
  } catch (err) {
    await this.editScreen(ctx, state, renderError(
      err instanceof Error ? err.message : String(err),
    ));
    return;
  }

  // Clear pending state
  state.pendingEntry       = undefined;
  state.pendingName        = undefined;
  state.pendingProviderKey = name;

  log.telegram.info(`[ConfigMenu] Provider added: "${name}"`);

  this.stateManager.set({ ...state, screen: "providers", breadcrumb: [] });
  await this.editScreen(ctx, state, renderSuccess(
    `Provider <b>${name}</b> added and active!\n` +
    `Protocol: <code>${providerType}</code> · Model: <code>${entry.defaultModel ?? "—"}</code>`,
  ));
}
```

- [ ] **Step 7: Update `removeProvider` to call `providerManager.deleteProvider()`**

Replace the existing `removeProvider` method body:

```typescript
private async removeProvider(
  ctx: Context,
  state: MenuState,
  providerKey: string,
): Promise<void> {
  try {
    if (this.providerManager) {
      await this.providerManager.deleteProvider(providerKey);
    } else {
      // Fallback: direct config mutation (no deregistration)
      const config = this.getConfig();
      if (providerKey === config.defaultProvider) {
        await this.editScreen(ctx, state, renderError(
          `Cannot remove the default provider (<b>${providerKey}</b>). Set another as default first.`,
        ));
        return;
      }
      delete config.providers[providerKey];
      await this.saveConfigFn(config);
    }
  } catch (err) {
    await this.editScreen(ctx, state, renderError(
      err instanceof Error ? err.message : String(err),
    ));
    return;
  }

  log.telegram.info(`[ConfigMenu] Removed provider "${providerKey}"`);
  this.stateManager.back(state.userId);
  await this.editScreen(ctx, state, renderProviders(this.getConfig(), this.lastHealth));
}
```

- [ ] **Step 8: Build TypeScript to check for errors**

```bash
npm run build 2>&1 | head -40
```

Fix any type errors. Common issues: `pendingName` not in `MenuState`, `renderAddProviderName` not imported, `isReservedOrDuplicate` not a method yet.

- [ ] **Step 9: Manual smoke test via Telegram**
1. Start the bot: `npm run dev`
2. Send `/config` in Telegram
3. Tap `📡 Providers` → `➕ Add Provider`
4. Bot should ask for a name → type `my-test-openai` → should advance to protocol picker
5. Try entering a reserved name like `anthropic` → should show error message
6. Complete the add flow → confirm provider appears in list

- [ ] **Step 10: Commit**

```bash
git add src/gateway/adapters/telegram-config/state.ts \
        src/gateway/adapters/telegram-config/menu.ts \
        src/gateway/adapters/telegram-config/screens.ts
git commit -m "feat(telegram): use ProviderManager in config menu — name step, hot-register, deregister"
```

---

## Task 6: TUI `/provider` command

**Files:**
- Create: `src/cli/v2/commands/handlers/provider.ts`
- Modify: `src/cli/v2/commands/registry.ts`
- Create: `__tests__/cli/v2/commands/provider.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/cli/v2/commands/provider.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  handleProviderList,
  handleProviderTest,
  handleProviderDelete,
} from "../../../../src/cli/v2/commands/handlers/provider.js";
import type { CommandContext } from "../../../../src/cli/v2/commands/registry.js";

function makeManager(overrides: Record<string, unknown> = {}) {
  return {
    listProviders: vi.fn().mockReturnValue([
      { name: "anthropic", profile: "anthropic", activeModel: "claude-sonnet-4-6", isDefault: true, health: "CLOSED", source: "system" },
      { name: "my-openai", profile: "openai", activeModel: "gpt-5", isDefault: false, health: "CLOSED", source: "custom" },
    ]),
    testProvider: vi.fn().mockResolvedValue({ ok: true, latencyMs: 42 }),
    deleteProvider: vi.fn().mockResolvedValue(undefined),
    addProvider: vi.fn().mockResolvedValue(undefined),
    editProvider: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function makeCtx(managerOverrides: Record<string, unknown> = {}): CommandContext {
  const manager = makeManager(managerOverrides);
  return {
    getOwlGateway: () => ({
      getProviderManager: () => manager,
      getWorkspacePath: () => "/tmp/test",
      getConfig: () => ({ providers: {} }),
    } as any),
    bridge: {
      emit: vi.fn(),
      openPanel: vi.fn(),
      closePanel: vi.fn(),
      requestOnboardingView: vi.fn(),
    },
    getStore: vi.fn(),
    getMemoryRepo: vi.fn(),
    getMcpManager: vi.fn(),
  } as unknown as CommandContext;
}

describe("/provider list", () => {
  it("returns a panel with all providers", async () => {
    const ctx = makeCtx();
    const result = await handleProviderList(ctx, []);
    expect(result.kind).toBe("panel");
    if (result.kind !== "panel") return;
    expect(result.payload.items).toHaveLength(2);
    const labels = result.payload.items.map((i) => i.label);
    expect(labels).toContain("anthropic");
    expect(labels).toContain("my-openai");
  });

  it("marks the default provider with a star", async () => {
    const ctx = makeCtx();
    const result = await handleProviderList(ctx, []);
    if (result.kind !== "panel") throw new Error("not a panel");
    const anthropic = result.payload.items.find((i) => i.label === "anthropic");
    expect(anthropic?.meta).toContain("★");
  });
});

describe("/provider test", () => {
  it("returns success message with latency on ok:true", async () => {
    const ctx = makeCtx();
    const result = await handleProviderTest(ctx, ["anthropic"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") return;
    expect(result.text).toMatch(/✅/);
    expect(result.text).toMatch(/42ms/);
  });

  it("returns error kind when no provider name given", async () => {
    const ctx = makeCtx();
    const result = await handleProviderTest(ctx, []);
    expect(result.kind).toBe("error");
  });

  it("returns error message on test failure", async () => {
    const ctx = makeCtx({ testProvider: vi.fn().mockResolvedValue({ ok: false, latencyMs: 100, error: "connection refused" }) });
    const result = await handleProviderTest(ctx, ["anthropic"]);
    expect(result.kind).toBe("system-message");
    if (result.kind !== "system-message") return;
    expect(result.text).toMatch(/❌/);
    expect(result.text).toMatch(/connection refused/);
  });
});

describe("/provider delete", () => {
  it("returns error when no provider name given", async () => {
    const ctx = makeCtx();
    const result = await handleProviderDelete(ctx, []);
    expect(result.kind).toBe("error");
  });

  it("calls deleteProvider and returns success message", async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    const ctx = makeCtx({ deleteProvider: deleteFn });
    const result = await handleProviderDelete(ctx, ["my-openai"]);
    expect(deleteFn).toHaveBeenCalledWith("my-openai");
    expect(result.kind).toBe("system-message");
  });

  it("returns error message when deleteProvider throws", async () => {
    const ctx = makeCtx({ deleteProvider: vi.fn().mockRejectedValue(new Error("cannot delete default")) });
    const result = await handleProviderDelete(ctx, ["anthropic"]);
    expect(result.kind).toBe("error");
    if (result.kind !== "error") return;
    expect(result.text).toMatch(/cannot delete default/i);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npx vitest run __tests__/cli/v2/commands/provider.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/cli/v2/commands/handlers/provider.ts`**

```typescript
/**
 * /provider command handlers — TUI provider CRUD.
 *
 * All operations delegate to ProviderManager (via getOwlGateway().getProviderManager()).
 * The handler never touches config or registry directly.
 */

import type { CommandHandler, CommandContext } from "../registry.js";
import type { ProviderStatus } from "../../../../providers/manager.js";
import type { PanelItem } from "../../panels/Panel.js";

function healthDot(health: ProviderStatus["health"]): string {
  switch (health) {
    case "CLOSED":       return "✅";
    case "HALF_OPEN":    return "⚡";
    case "OPEN":         return "❌";
    case "unconfigured": return "○";
  }
}

// ─── /provider list ───────────────────────────────────────────────

export const handleProviderList: CommandHandler = async (ctx) => {
  const manager = ctx.getOwlGateway().getProviderManager();
  const statuses = manager.listProviders();

  const items: PanelItem[] = statuses.map((s) => ({
    id: s.name,
    label: s.name,
    meta: [
      healthDot(s.health),
      s.isDefault ? "★" : "",
      s.activeModel,
      `[${s.source}]`,
    ].filter(Boolean).join(" "),
    data: s,
    edit: {
      kind: "drill" as const,
      onEnter: () => openProviderDetail(ctx, s.name),
    },
  }));

  return {
    kind: "panel",
    payload: {
      title: "/provider",
      items,
      emptyText: "No providers configured. Use /provider add to add one.",
      actions: [
        {
          key: "t",
          label: "test",
          handler: async (item) => {
            const result = await manager.testProvider(item.id);
            ctx.bridge.emit({
              kind: "notice",
              source: "command",
              text: result.ok
                ? `✅ ${item.id} OK (${result.latencyMs}ms)`
                : `❌ ${item.id} FAIL: ${result.error ?? "unknown error"}`,
              severity: result.ok ? "info" : "error",
            });
          },
        },
        {
          key: "d",
          label: "delete",
          confirm: "Type the provider name to confirm deletion",
          handler: async (item) => {
            try {
              await manager.deleteProvider(item.id);
              ctx.bridge.emit({
                kind: "notice",
                source: "command",
                text: `Provider "${item.id}" removed.`,
                severity: "info",
              });
              ctx.bridge.closePanel();
            } catch (err) {
              ctx.bridge.emit({
                kind: "notice",
                source: "command",
                text: (err as Error).message,
                severity: "error",
              });
            }
          },
        },
      ],
    },
  };
};

// ─── /provider test <name> ────────────────────────────────────────

export const handleProviderTest: CommandHandler = async (ctx, args) => {
  const name = args[0];
  if (!name) {
    return { kind: "error", text: "Usage: /provider test <name>" };
  }
  const manager = ctx.getOwlGateway().getProviderManager();
  const result  = await manager.testProvider(name);
  const text = result.ok
    ? `✅ ${name} responded OK in ${result.latencyMs}ms`
    : `❌ ${name} failed in ${result.latencyMs}ms: ${result.error ?? "unknown error"}`;
  return { kind: "system-message", text };
};

// ─── /provider delete <name> ─────────────────────────────────────

export const handleProviderDelete: CommandHandler = async (ctx, args) => {
  const name = args[0];
  if (!name) {
    return { kind: "error", text: "Usage: /provider delete <name>" };
  }
  const manager = ctx.getOwlGateway().getProviderManager();
  try {
    await manager.deleteProvider(name);
    return { kind: "system-message", text: `Provider "${name}" removed.` };
  } catch (err) {
    return { kind: "error", text: (err as Error).message };
  }
};

// ─── /provider edit <name> <field> <value> ───────────────────────

export const handleProviderEdit: CommandHandler = async (ctx, args) => {
  const [name, field, ...rest] = args;
  const value = rest.join(" ");

  if (!name || !field || !value) {
    return { kind: "error", text: "Usage: /provider edit <name> <key|model|url> <value>" };
  }

  const manager = ctx.getOwlGateway().getProviderManager();
  const updates: Record<string, string> = {};

  if (field === "key")   updates.apiKey      = value;
  else if (field === "model") updates.activeModel = value;
  else if (field === "url")   updates.baseUrl     = value;
  else return { kind: "error", text: "Field must be one of: key, model, url" };

  try {
    await manager.editProvider(name, updates);
    return { kind: "system-message", text: `${name}.${field} updated.` };
  } catch (err) {
    return { kind: "error", text: (err as Error).message };
  }
};

// ─── Detail drill-down (opens new panel) ─────────────────────────

function openProviderDetail(ctx: CommandContext, name: string): void {
  const manager  = ctx.getOwlGateway().getProviderManager();
  const statuses = manager.listProviders();
  const status   = statuses.find((s) => s.name === name);
  if (!status) return;

  const items: PanelItem[] = [
    { id: "profile",     label: "Protocol",      meta: status.profile },
    { id: "activeModel", label: "Active model",  meta: status.activeModel,
      edit: { kind: "string", currentValue: status.activeModel, onSubmit: (v) => updateField(ctx, name, "model", v) } },
    { id: "health",      label: "Health",        meta: `${healthDot(status.health)} ${status.health}` },
    { id: "source",      label: "Source",        meta: status.source },
    { id: "default",     label: "Default",       meta: status.isDefault ? "yes ★" : "no" },
  ];

  if (status.source === "custom") {
    items.push({
      id: "apiKey",
      label: "API key",
      meta: "••••••••",
      edit: { kind: "string", currentValue: "", mask: true, onSubmit: (v) => updateField(ctx, name, "key", v) },
    });
  } else {
    items.push({
      id: "apiKey",
      label: "API key",
      meta: "••••••••",
      edit: { kind: "string", currentValue: "", mask: true, onSubmit: (v) => updateField(ctx, name, "key", v) },
    });
  }

  ctx.bridge.openPanel(`provider:${name}`, {
    title: `/provider · ${name}`,
    items,
    emptyText: "",
    actions: [
      {
        key: "t",
        label: "test connection",
        handler: async () => {
          const result = await manager.testProvider(name);
          ctx.bridge.emit({
            kind: "notice",
            source: "command",
            text: result.ok
              ? `✅ ${name} OK (${result.latencyMs}ms)`
              : `❌ ${name} FAIL: ${result.error ?? "unknown error"}`,
            severity: result.ok ? "info" : "error",
          });
        },
      },
    ],
  });
}

async function updateField(ctx: CommandContext, providerName: string, field: string, value: string): Promise<void> {
  const manager = ctx.getOwlGateway().getProviderManager();
  try {
    const updates: Record<string, string> = {};
    if (field === "key")   updates.apiKey      = value;
    if (field === "model") updates.activeModel = value;
    if (field === "url")   updates.baseUrl     = value;
    await manager.editProvider(providerName, updates);
    ctx.bridge.emit({ kind: "notice", source: "command", text: `${providerName}.${field} updated.`, severity: "info" });
  } catch (err) {
    ctx.bridge.emit({ kind: "notice", source: "command", text: (err as Error).message, severity: "error" });
  }
}
```

- [ ] **Step 4: Register `/provider` in `src/cli/v2/commands/registry.ts`**

Add the import at the top:

```typescript
import {
  handleProviderList,
  handleProviderTest,
  handleProviderDelete,
  handleProviderEdit,
} from "./handlers/provider.js";
```

Add the command spec to the `REGISTRY` array (after `/owl`):

```typescript
{
  name: "/provider",
  description: "Manage AI providers — add, list, edit, delete, test",
  subcommands: [
    { name: "list",   description: "List all configured providers",                    handler: handleProviderList },
    { name: "test",   description: "Test provider connection",  args: [{ name: "<name>" }], handler: handleProviderTest },
    { name: "delete", description: "Delete a provider",         args: [{ name: "<name>" }], handler: handleProviderDelete },
    { name: "edit",   description: "Edit provider field",       args: [{ name: "<name>" }, { name: "<key|model|url>" }, { name: "<value>" }], handler: handleProviderEdit },
  ],
  handler: handleProviderList,
},
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
npx vitest run __tests__/cli/v2/commands/provider.test.ts
```

Expected: PASS (all tests)

- [ ] **Step 6: Run the full test suite**

```bash
npx vitest run
```

Expected: all tests pass with no regressions.

- [ ] **Step 7: Smoke test the TUI**

```bash
npm run dev
```

In the TUI: type `/provider` → should see a list panel. Type `/provider test anthropic` → should attempt a health check.

- [ ] **Step 8: Commit**

```bash
git add src/cli/v2/commands/handlers/provider.ts \
        src/cli/v2/commands/registry.ts \
        __tests__/cli/v2/commands/provider.test.ts
git commit -m "feat(tui): /provider command — list, test, delete, edit panels"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task that covers it |
|---|---|
| `deregister()` on registry | Task 1 |
| Multi-dir ModelLoader with system name reservation | Task 2 |
| ProviderManager: addProvider, editProvider, deleteProvider, listProviders, testProvider | Task 3 |
| Gateway exposes ProviderManager | Task 4 |
| ModelLoader initialized with workspace dir at boot | Task 4 |
| Telegram: name input step before protocol picker | Task 5 |
| Telegram: hot-register on add via ProviderManager | Task 5 |
| Telegram: deregister on delete via ProviderManager | Task 5 |
| Telegram: system name reservation check in name step | Task 5 |
| TUI `/provider list` with health dots | Task 6 |
| TUI `/provider test <name>` | Task 6 |
| TUI `/provider delete <name>` | Task 6 |
| TUI `/provider edit <name> <field> <value>` | Task 6 |
| Provider detail drill-down panel | Task 6 |
| Block delete of default provider | Task 3 + Task 5 |
| Workspace model files for custom providers | Task 3 (`customConfig`) |
| No restart needed | Tasks 1 + 3 (hot-register/deregister) |

### Placeholder scan

No placeholders found. All code blocks are complete.

### Type consistency

- `ProviderStatus` defined in Task 3 (`manager.ts`), imported in Task 6 (`provider.ts`) — consistent.
- `AddProviderInput.profile` used in Task 3 and Task 5 `finalizeProviderAdd` — consistent.
- `deregister(name: string): void` defined in Task 1, called in Task 3 — consistent.
- `isSystemName(name: string): boolean` defined in Task 2, called in Task 3 — consistent.
- `isReservedOrDuplicate(name, config)` added to Task 3 and called in Task 5 — consistent.
- `initModelLoader` / `resetModelLoader` defined in Task 2, used in Task 3 and Task 4 — consistent.
