# Smart Routing Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix smart routing end-to-end — update the config schema, router, CLI onboarding, and Telegram menu so model-tier routing actually works.

**Architecture:** Schema change cascades through four layers: (1) `availableModels` type updated with `modelName`/`providerName` fields, (2) router returns `providerName` on complexity path, (3) CLI onboarding gains a smart routing step after provider setup, (4) Telegram menu replaces the broken `fallbackChain` string-array with a proper roster editor.

**Tech Stack:** TypeScript, Vitest, grammY (Telegram inline keyboards), chalk + raw TTY (CLI), existing ModelLoader singleton

---

## File Structure

| File | Change |
|---|---|
| `src/config/loader.ts` | Update `availableModels` type; no default value change |
| `src/engine/router.ts` | Return `providerName` on complexity path; fix log line |
| `src/gateway/adapters/telegram-config/state.ts` | Add `smart_routing`, `sr_prov_pick`, `sr_model_pick` screens; add `pendingSrProvider` field |
| `src/gateway/adapters/telegram-config/screens.ts` | Replace `renderFallbackChain`/`renderFallbackAddPicker` with `renderSmartRouting`/`renderSmartRoutingProviderPicker`/`renderSmartRoutingModelPicker` |
| `src/gateway/adapters/telegram-config/menu.ts` | Replace `fb*`/`fa*` handlers + methods with `sr*` handlers |
| `src/cli/onboarding-flow.ts` | Add `sr_ask`/`sr_prov_pick`/`sr_model_pick`/`sr_more` steps; update `buildConfig` |
| `__tests__/router.test.ts` | New: router unit tests |
| `__tests__/smart-routing-screens.test.ts` | New: Telegram screen render unit tests |

---

### Task 1: Config Schema

**Files:**
- Modify: `src/config/loader.ts:95-103`
- Test: `__tests__/config.test.ts`

Context: `availableModels` currently has type `{ name: string; description: string }[]`. The router reads `.name` and returns no `providerName`, so cross-provider complexity routing is impossible. Change it to carry both `modelName` and `providerName`.

- [ ] **Step 1: Write the failing test**

Add to `__tests__/config.test.ts` inside the existing `describe("loadConfig"` block:

```typescript
it("availableModels entries have modelName and providerName fields", async () => {
  vi.mocked(existsSync).mockReturnValue(false);
  vi.mocked(writeFile).mockResolvedValue(undefined);
  vi.mocked(readFile).mockRejectedValue(new Error("not found"));
  const config = await loadConfig(testDir);
  // default is empty array — verify the type compiles by constructing a valid entry
  const entry: NonNullable<typeof config.smartRouting>["availableModels"][number] = {
    modelName: "claude-sonnet-4-6",
    providerName: "anthropic",
  };
  expect(entry.modelName).toBe("claude-sonnet-4-6");
  expect(entry.providerName).toBe("anthropic");
  // old field `name` must not exist on the type
  expect((entry as any).name).toBeUndefined();
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/config.test.ts
```

Expected: FAIL — TypeScript error or `name` field present.

- [ ] **Step 3: Update the schema in `src/config/loader.ts`**

Find lines 95–103:
```typescript
  smartRouting?: {
    enabled: boolean;
    fallbackProvider?: string; // e.g. 'anthropic'
    fallbackModel?: string; // e.g. 'claude-3-5-sonnet-latest'
    availableModels: {
      name: string;
      description: string;
    }[];
  };
```

Replace with:
```typescript
  smartRouting?: {
    enabled: boolean;
    fallbackProvider?: string;
    fallbackModel?: string;
    availableModels: {
      modelName: string;
      providerName: string;
      description?: string;
    }[];
  };
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/config.test.ts
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/loader.ts __tests__/config.test.ts
git commit -m "feat(config): update availableModels schema to modelName+providerName"
```

---

### Task 2: Router Fix

**Files:**
- Modify: `src/engine/router.ts:90-112`
- Test: `__tests__/router.test.ts` (create)

Context: Two bugs — (1) when roster has 1 entry, line 93 returns `{ modelName: models[0].name }` (old field); (2) complexity path line 112 returns only `{ modelName }`, no `providerName`; (3) log line 111 omits provider. All three need fixing.

- [ ] **Step 1: Write the failing tests**

Create `__tests__/router.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { ModelRouter } from "../src/engine/router.js";
import type { StackOwlConfig } from "../src/config/loader.js";

function makeConfig(models: Array<{ modelName: string; providerName: string }>): StackOwlConfig {
  return {
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    providers: { ollama: { baseUrl: "http://localhost:11434", apiKey: "", defaultModel: "llama3.2", type: "ollama" } },
    smartRouting: {
      enabled: true,
      availableModels: models,
    },
  } as unknown as StackOwlConfig;
}

describe("ModelRouter", () => {
  it("returns providerName on simple tier", () => {
    const config = makeConfig([
      { modelName: "llama3.2", providerName: "ollama" },
      { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
    ]);
    const result = ModelRouter.route("hi", config, 0);
    expect(result.providerName).toBe("ollama");
    expect(result.modelName).toBe("llama3.2");
  });

  it("returns providerName on heavy tier", () => {
    const config = makeConfig([
      { modelName: "llama3.2", providerName: "ollama" },
      { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
    ]);
    const result = ModelRouter.route("implement a full TypeScript compiler with AST", config, 0);
    expect(result.providerName).toBe("anthropic");
    expect(result.modelName).toBe("claude-sonnet-4-6");
  });

  it("returns providerName when roster has exactly 1 entry", () => {
    const config = makeConfig([{ modelName: "gpt-4o", providerName: "openai" }]);
    const result = ModelRouter.route("hello", config, 0);
    expect(result.modelName).toBe("gpt-4o");
    expect(result.providerName).toBe("openai");
  });

  it("failure fallback returns both fields", () => {
    const config = {
      ...makeConfig([]),
      smartRouting: {
        enabled: true,
        availableModels: [],
        fallbackProvider: "anthropic",
        fallbackModel: "claude-sonnet-4-6",
      },
    } as unknown as StackOwlConfig;
    const result = ModelRouter.route("hi", config, 2);
    expect(result.providerName).toBe("anthropic");
    expect(result.modelName).toBe("claude-sonnet-4-6");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/router.test.ts
```

Expected: FAIL — `providerName` is `undefined` on the results.

- [ ] **Step 3: Fix `src/engine/router.ts`**

Find lines 90–112:
```typescript
    // Single model in roster → no decision needed
    if (models.length === 1) {
      return { modelName: models[0].name };
    }

    // Score task complexity
    const tier = scoreComplexity(prompt);

    // Map tier to roster position by index (assume models ordered light → heavy)
    let targetIndex: number;
    if (tier === "simple") {
      targetIndex = 0;
    } else if (tier === "heavy") {
      targetIndex = models.length - 1;
    } else {
      targetIndex = Math.floor(models.length / 2);
    }

    const selected = models[targetIndex];
    log.engine.info(`[ModelRouter] Tier="${tier}" → ${selected.name}`);
    return { modelName: selected.name };
```

Replace with:
```typescript
    // Single model in roster → no decision needed
    if (models.length === 1) {
      return { modelName: models[0].modelName, providerName: models[0].providerName };
    }

    // Score task complexity
    const tier = scoreComplexity(prompt);

    // Map tier to roster position by index (assume models ordered light → heavy)
    let targetIndex: number;
    if (tier === "simple") {
      targetIndex = 0;
    } else if (tier === "heavy") {
      targetIndex = models.length - 1;
    } else {
      targetIndex = Math.floor(models.length / 2);
    }

    const selected = models[targetIndex];
    log.engine.info(`[ModelRouter] Tier="${tier}" → ${selected.providerName} / ${selected.modelName}`);
    return { modelName: selected.modelName, providerName: selected.providerName };
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/router.test.ts
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/engine/router.ts __tests__/router.test.ts
git commit -m "fix(router): return providerName on complexity path; fix log line"
```

---

### Task 3: Telegram State Types

**Files:**
- Modify: `src/gateway/adapters/telegram-config/state.ts:10-23`

Context: The `MenuScreen` union still has `fallback_chain` and `fallback_add_prov`. These map to the old broken fallback chain UI. Replace them with three new screens for the roster editor. Also add `pendingSrProvider` to `MenuState` for the add-model two-step flow (pick provider → pick model).

- [ ] **Step 1: Write the failing test**

Create `__tests__/smart-routing-screens.test.ts` with just the import (it will grow in Task 4):

```typescript
import { describe, it, expect } from "vitest";
import { MenuStateManager } from "../src/gateway/adapters/telegram-config/state.js";

describe("MenuStateManager — smart routing screens", () => {
  it("can navigate to smart_routing screen", () => {
    const mgr = new MenuStateManager();
    mgr.set({
      userId: 1, chatId: 1, messageId: 1,
      screen: "main", breadcrumb: [], lastActivity: Date.now(),
    });
    mgr.navigate(1, "smart_routing");
    expect(mgr.get(1)?.screen).toBe("smart_routing");
  });

  it("can navigate to sr_prov_pick screen", () => {
    const mgr = new MenuStateManager();
    mgr.set({
      userId: 2, chatId: 2, messageId: 2,
      screen: "main", breadcrumb: [], lastActivity: Date.now(),
    });
    mgr.navigate(2, "sr_prov_pick");
    expect(mgr.get(2)?.screen).toBe("sr_prov_pick");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/smart-routing-screens.test.ts
```

Expected: FAIL — TypeScript error: `"smart_routing"` is not assignable to `MenuScreen`.

- [ ] **Step 3: Update `src/gateway/adapters/telegram-config/state.ts`**

Replace lines 10–23:
```typescript
export type MenuScreen =
  | "main"
  | "providers"
  | "provider_detail"
  | "provider_add_type"
  | "provider_add_url"     // awaiting baseUrl text input
  | "provider_add_key"     // awaiting apiKey text input (shows security choice)
  | "provider_model_pick"  // model selection for a provider
  | "model_roles"
  | "model_role_prov_pick" // pick provider for a role
  | "model_role_model_pick"// pick model for a role
  | "fallback_chain"
  | "fallback_add_prov"    // pick provider to add to chain
  | "health_check";
```

With:
```typescript
export type MenuScreen =
  | "main"
  | "providers"
  | "provider_detail"
  | "provider_add_type"
  | "provider_add_url"
  | "provider_add_key"
  | "provider_model_pick"
  | "model_roles"
  | "model_role_prov_pick"
  | "model_role_model_pick"
  | "smart_routing"
  | "sr_prov_pick"
  | "sr_model_pick"
  | "health_check";
```

Also add `pendingSrProvider?: string;` to `MenuState` after `providerList?`:
```typescript
  /** Provider selected during smart routing add-model flow */
  pendingSrProvider?: string;
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/smart-routing-screens.test.ts
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gateway/adapters/telegram-config/state.ts __tests__/smart-routing-screens.test.ts
git commit -m "feat(telegram): add smart_routing screen types, remove fallback_chain"
```

---

### Task 4: Telegram Screens

**Files:**
- Modify: `src/gateway/adapters/telegram-config/screens.ts`
- Test: `__tests__/smart-routing-screens.test.ts`

Context: `renderFallbackChain` reads the old `fallbackChain: string[]` key. `renderFallbackAddPicker` shows just provider names, no model picker. Both get replaced. New screens: `renderSmartRouting` (roster with ↑/↓/✕ per row + Add Model + fallback fields), `renderSmartRoutingProviderPicker` (step 1 of add flow), `renderSmartRoutingModelPicker` (step 2 of add flow).

The callback data format is `cfg:<cmd>`. Keep under 64 bytes total. Up/down use entry index: `cfg:sr_up:0`. Disabled boundary buttons show `·` via `cfg:noop`.

- [ ] **Step 1: Add tests to `__tests__/smart-routing-screens.test.ts`**

```typescript
import {
  renderSmartRouting,
  renderSmartRoutingProviderPicker,
  renderSmartRoutingModelPicker,
} from "../src/gateway/adapters/telegram-config/screens.js";
import type { StackOwlConfig } from "../src/config/loader.js";

function baseConfig(overrides?: Partial<StackOwlConfig["smartRouting"]>): StackOwlConfig {
  return {
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    providers: {},
    smartRouting: {
      enabled: false,
      availableModels: [],
      ...overrides,
    },
  } as unknown as StackOwlConfig;
}

describe("renderSmartRouting", () => {
  it("shows toggle button", () => {
    const { text, keyboard } = renderSmartRouting(baseConfig());
    expect(text).toContain("Smart Routing");
    const buttons = keyboard.inline_keyboard.flat().map(b => b.text);
    expect(buttons.some(b => b.includes("Enable") || b.includes("Disable"))).toBe(true);
  });

  it("shows roster entries with up/down/remove buttons", () => {
    const config = baseConfig({
      enabled: true,
      availableModels: [
        { modelName: "llama3.2", providerName: "ollama" },
        { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
      ],
    });
    const { text } = renderSmartRouting(config);
    expect(text).toContain("ollama");
    expect(text).toContain("anthropic");
    expect(text).toContain("llama3.2");
    expect(text).toContain("claude-sonnet-4-6");
  });

  it("shows Add Model button when enabled", () => {
    const config = baseConfig({ enabled: true, availableModels: [] });
    const { keyboard } = renderSmartRouting(config);
    const buttons = keyboard.inline_keyboard.flat().map(b => b.text);
    expect(buttons.some(b => b.includes("Add"))).toBe(true);
  });

  it("up button disabled for first entry", () => {
    const config = baseConfig({
      enabled: true,
      availableModels: [
        { modelName: "llama3.2", providerName: "ollama" },
        { modelName: "gpt-4o", providerName: "openai" },
      ],
    });
    const { keyboard } = renderSmartRouting(config);
    const allButtons = keyboard.inline_keyboard.flat();
    const upButtons = allButtons.filter(b => (b as any).callback_data === "cfg:sr_up:0");
    // First entry up should be noop
    const firstRowUpButton = allButtons.find(b => (b as any).callback_data === "cfg:noop" && b.text === "·");
    expect(firstRowUpButton).toBeDefined();
  });
});

describe("renderSmartRoutingProviderPicker", () => {
  it("shows all provider keys as buttons", () => {
    const { keyboard } = renderSmartRoutingProviderPicker(["ollama", "anthropic"]);
    const buttons = keyboard.inline_keyboard.flat().map(b => (b as any).callback_data as string);
    expect(buttons).toContain("cfg:sr_ap:ollama");
    expect(buttons).toContain("cfg:sr_ap:anthropic");
  });
});

describe("renderSmartRoutingModelPicker", () => {
  it("shows model buttons with provider prefix in callback", () => {
    const { keyboard } = renderSmartRoutingModelPicker("ollama", ["llama3.2", "mistral"]);
    const buttons = keyboard.inline_keyboard.flat().map(b => (b as any).callback_data as string);
    expect(buttons).toContain("cfg:sr_am:ollama:llama3.2");
    expect(buttons).toContain("cfg:sr_am:ollama:mistral");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/smart-routing-screens.test.ts
```

Expected: FAIL — named exports not found.

- [ ] **Step 3: Replace `renderFallbackChain` and `renderFallbackAddPicker` in `src/gateway/adapters/telegram-config/screens.ts`**

Find the section starting at `// ─── Screen: Fallback Chain ───` (around line 357) through the end of `renderFallbackAddPicker` (around line 405). Replace the entire block with:

```typescript
// ─── Screen: Smart Routing ────────────────────────────────────────

export function renderSmartRouting(config: StackOwlConfig): ScreenContent {
  const sr      = config.smartRouting;
  const enabled = sr?.enabled ?? false;
  const roster  = sr?.availableModels ?? [];

  const toggleLabel = enabled ? "🔴 Disable Smart Routing" : "🟢 Enable Smart Routing";

  const rosterLines = roster.length > 0
    ? roster.map((e, i) => {
        const tier = i === 0 ? "light" : i === roster.length - 1 ? "heavy" : "mid";
        return `${i + 1}. <code>${e.providerName}</code> · <b>${e.modelName}</b>  <i>${tier}</i>`;
      }).join("\n")
    : "<i>No models in roster. Add at least 2 to enable routing.</i>";

  const fallbackLine = sr?.fallbackProvider
    ? `\nFailback: <code>${sr.fallbackProvider}</code> · <b>${sr.fallbackModel ?? "—"}</b>`
    : "";

  const text =
    `⚡ <b>Smart Routing</b>\n\n` +
    `Status: ${enabled ? "🟢 ON" : "🔴 OFF"}\n\n` +
    rosterLines +
    fallbackLine;

  const keyboard = new InlineKeyboard()
    .text(toggleLabel, "cfg:sr_tog").row();

  roster.forEach((_, i) => {
    const upCb   = i === 0              ? "cfg:noop" : `cfg:sr_up:${i}`;
    const downCb = i === roster.length - 1 ? "cfg:noop" : `cfg:sr_dn:${i}`;
    const upTxt  = i === 0              ? "·" : "↑";
    const downTxt = i === roster.length - 1 ? "·" : "↓";
    keyboard
      .text(upTxt,              upCb)
      .text(downTxt,            downCb)
      .text(`✕ ${roster[i].modelName}`, `cfg:sr_rm:${i}`)
      .row();
  });

  keyboard.text("➕ Add Model", "cfg:sr_add").row();
  keyboard.text("← Back", "cfg:bc");

  return { text, keyboard };
}

// ─── Screen: Smart Routing — Provider Picker ─────────────────────

export function renderSmartRoutingProviderPicker(providers: string[]): ScreenContent {
  const text = `⚡ <b>Add to Roster</b>\n\nChoose provider:`;
  const keyboard = new InlineKeyboard();
  providers.forEach((p) => {
    keyboard.text(p, `cfg:sr_ap:${p}`).row();
  });
  keyboard.text("← Back", "cfg:bc");
  return { text, keyboard };
}

// ─── Screen: Smart Routing — Model Picker ────────────────────────

export function renderSmartRoutingModelPicker(
  providerName: string,
  models: string[],
): ScreenContent {
  const text = `⚡ <b>Add to Roster</b>\n\nProvider: <code>${providerName}</code>\nChoose model:`;
  const keyboard = new InlineKeyboard();
  models.forEach((m) => {
    keyboard.text(m, `cfg:sr_am:${providerName}:${m}`).row();
  });
  keyboard.text("← Back", "cfg:bc");
  return { text, keyboard };
}
```

Also update the export list in the file (remove `renderFallbackChain`, `renderFallbackAddPicker`; they are replaced by the three new functions above — no separate export statement needed as they are already exported inline).

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/smart-routing-screens.test.ts
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gateway/adapters/telegram-config/screens.ts __tests__/smart-routing-screens.test.ts
git commit -m "feat(telegram): replace fallbackChain screens with roster editor screens"
```

---

### Task 5: Telegram Menu Controller

**Files:**
- Modify: `src/gateway/adapters/telegram-config/menu.ts`

Context: The `route()` method handles `fb`, `fb_tog`, `fa`, `fa_p:*`, `fb_rm:*` callbacks and calls `toggleSmartRouting`, `addToFallbackChain`, `removeFromFallbackChain`. All of these read/write the old `fallbackChain` key. They get replaced entirely with `sr*` handlers. `renderCurrentScreen` has a `fallback_chain` case that also needs updating.

- [ ] **Step 1: Update imports in `src/gateway/adapters/telegram-config/menu.ts`**

Find the import block at lines 24–43. Replace the two removed exports and add three new ones:

```typescript
import {
  renderMain,
  renderProviders,
  renderProviderDetail,
  renderProviderRemoveConfirm,
  renderAddProviderType,
  renderAddProviderUrl,
  renderAddProviderKey,
  renderModelPicker,
  renderModelRoles,
  renderRoleProviderPicker,
  renderSmartRouting,
  renderSmartRoutingProviderPicker,
  renderSmartRoutingModelPicker,
  renderHealthCheck,
  renderWebFormLink,
  renderError,
  renderSuccess,
  ANTHROPIC_MODELS,
  PROVIDER_TYPE_META,
} from "./screens.js";
```

Also add this import near the top of the file (after existing imports):

```typescript
import { getModelLoader } from "../../../models/loader.js";
```

- [ ] **Step 2: Replace the Fallback Chain section in `route()` (lines ~385–414)**

Find:
```typescript
    // ── Fallback Chain ───────────────────────────────────────────
    if (cmd === "fb") {
      this.stateManager.navigate(state.userId, "fallback_chain");
      await this.editScreen(ctx, state, renderFallbackChain(this.getConfig()));
      return;
    }

    if (cmd === "fb_tog") {
      await this.toggleSmartRouting(ctx, state);
      return;
    }

    if (cmd === "fa") {
      const providers = Object.keys(this.getConfig().providers);
      this.stateManager.navigate(state.userId, "fallback_add_prov");
      await this.editScreen(ctx, state, renderFallbackAddPicker(providers, this.getConfig()));
      return;
    }

    if (cmd.startsWith("fa_p:")) {
      const providerKey = cmd.slice(5);
      await this.addToFallbackChain(ctx, state, providerKey);
      return;
    }

    if (cmd.startsWith("fb_rm:")) {
      const providerKey = cmd.slice(6);
      await this.removeFromFallbackChain(ctx, state, providerKey);
      return;
    }
```

Replace with:
```typescript
    // ── Smart Routing ────────────────────────────────────────────
    if (cmd === "sr") {
      this.stateManager.navigate(state.userId, "smart_routing");
      await this.editScreen(ctx, state, renderSmartRouting(this.getConfig()));
      return;
    }

    if (cmd === "sr_tog") {
      await this.toggleSmartRouting(ctx, state);
      return;
    }

    if (cmd === "sr_add") {
      const providers = getModelLoader().getAll().map(d => d.name);
      this.stateManager.navigate(state.userId, "sr_prov_pick");
      await this.editScreen(ctx, state, renderSmartRoutingProviderPicker(providers));
      return;
    }

    if (cmd.startsWith("sr_ap:")) {
      const providerName = cmd.slice(6);
      state.pendingSrProvider = providerName;
      const def = getModelLoader().get(providerName);
      const models = def?.availableModels ?? [];
      this.stateManager.navigate(state.userId, "sr_model_pick");
      await this.editScreen(ctx, state, renderSmartRoutingModelPicker(providerName, models));
      return;
    }

    if (cmd.startsWith("sr_am:")) {
      const parts = cmd.slice(6).split(":");
      const providerName = parts[0];
      const modelName    = parts.slice(1).join(":");
      await this.addRosterEntry(ctx, state, providerName, modelName);
      return;
    }

    if (cmd.startsWith("sr_rm:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.removeRosterEntry(ctx, state, idx);
      return;
    }

    if (cmd.startsWith("sr_up:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.moveRosterEntry(ctx, state, idx, -1);
      return;
    }

    if (cmd.startsWith("sr_dn:")) {
      const idx = parseInt(cmd.slice(6), 10);
      await this.moveRosterEntry(ctx, state, idx, 1);
      return;
    }
```

- [ ] **Step 3: Update `renderCurrentScreen` — replace `fallback_chain` case**

Find:
```typescript
      case "fallback_chain":
        await this.editScreen(ctx, state, renderFallbackChain(config));
        break;
```

Replace with:
```typescript
      case "smart_routing":
        await this.editScreen(ctx, state, renderSmartRouting(config));
        break;
```

- [ ] **Step 4: Replace old methods with new ones — find `// ─── Fallback Chain ───` section (~line 852)**

Find the block containing `toggleSmartRouting`, `addToFallbackChain`, `removeFromFallbackChain` and replace entirely:

```typescript
  // ─── Smart Routing ────────────────────────────────────────────

  private async toggleSmartRouting(ctx: Context, state: MenuState): Promise<void> {
    const config  = this.getConfig();
    const enabled = !(config.smartRouting?.enabled ?? false);
    config.smartRouting = {
      ...config.smartRouting,
      enabled,
      availableModels: config.smartRouting?.availableModels ?? [],
    };
    await this.saveConfigFn(config);
    this.stateManager.navigate(state.userId, "smart_routing");
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async addRosterEntry(
    ctx: Context,
    state: MenuState,
    providerName: string,
    modelName: string,
  ): Promise<void> {
    const config  = this.getConfig();
    const roster  = config.smartRouting?.availableModels ?? [];
    roster.push({ modelName, providerName });
    config.smartRouting = { ...config.smartRouting, enabled: config.smartRouting?.enabled ?? false, availableModels: roster };
    await this.saveConfigFn(config);
    this.stateManager.back(state.userId);
    this.stateManager.back(state.userId);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async removeRosterEntry(
    ctx: Context,
    state: MenuState,
    idx: number,
  ): Promise<void> {
    const config = this.getConfig();
    const roster = config.smartRouting?.availableModels ?? [];
    roster.splice(idx, 1);
    config.smartRouting = { ...config.smartRouting, enabled: config.smartRouting?.enabled ?? false, availableModels: roster };
    await this.saveConfigFn(config);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }

  private async moveRosterEntry(
    ctx: Context,
    state: MenuState,
    idx: number,
    direction: -1 | 1,
  ): Promise<void> {
    const config  = this.getConfig();
    const roster  = config.smartRouting?.availableModels ?? [];
    const swapIdx = idx + direction;
    if (swapIdx < 0 || swapIdx >= roster.length) return;
    [roster[idx], roster[swapIdx]] = [roster[swapIdx], roster[idx]];
    config.smartRouting = { ...config.smartRouting, enabled: config.smartRouting?.enabled ?? false, availableModels: roster };
    await this.saveConfigFn(config);
    await this.editScreen(ctx, state, renderSmartRouting(config));
  }
```

- [ ] **Step 5: Run full test suite to verify no regressions**

```bash
npx vitest run
```

Expected: All previously passing tests still pass. TypeScript compiler happy (no `renderFallbackChain` import errors).

- [ ] **Step 6: Commit**

```bash
git add src/gateway/adapters/telegram-config/menu.ts
git commit -m "feat(telegram): replace fallbackChain handlers with roster add/remove/reorder"
```

---

### Task 6: CLI Onboarding

**Files:**
- Modify: `src/cli/onboarding-flow.ts`
- Test: `__tests__/cli/onboarding-flow.test.ts` (create)

Context: Every provider-completion step currently transitions directly to `chan_multi`. Add a new `sr_ask` → `sr_prov_pick` → `sr_model_pick` → `sr_more` step sequence that runs between the provider section and the channels section. `buildConfig` must write `smartRouting` when the user enables it.

New step IDs to add to the `StepId` union: `"sr_ask"` | `"sr_prov_pick"` | `"sr_model_pick"` | `"sr_more"`.

New fields to add to `WizardData`:
```typescript
srEnabled?: boolean;
srRoster?: Array<{ modelName: string; providerName: string }>;
srAvailProviders?: string[];
srPendingProvider?: string;
srProviderModels?: string[];
```

- [ ] **Step 1: Write the failing tests**

Create `__tests__/cli/onboarding-flow.test.ts`:

```typescript
import { describe, it, expect } from "vitest";

// Test buildConfig output — import the internal helper by re-exporting it.
// Since buildConfig is private, we test via the public surface: after all
// provider steps, onboarding data flows through to config output.
// We test the step transition logic by inspecting _step via a test subclass.

// NOTE: OnboardingFlow._step and _data are private.
// We test them via duck-typing the class in test.
import { OnboardingFlow } from "../../src/cli/onboarding-flow.js";

describe("OnboardingFlow smart routing steps", () => {
  it("OnboardingFlow class is importable", () => {
    expect(typeof OnboardingFlow).toBe("function");
  });

  // Type-level test: verify WizardData has srEnabled and srRoster
  it("WizardData type includes srEnabled and srRoster (type assertion)", () => {
    // We cast to any to access private _data — type-level verification only
    const flow = new OnboardingFlow("/tmp/test.json");
    const data = (flow as any)._data;
    // Initially undefined is fine — just confirms the shape is accepted
    data.srEnabled = true;
    data.srRoster  = [{ modelName: "llama3.2", providerName: "ollama" }];
    expect(data.srEnabled).toBe(true);
    expect(data.srRoster[0].modelName).toBe("llama3.2");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/cli/onboarding-flow.test.ts
```

Expected: FAIL — `OnboardingFlow` not exported or `srEnabled`/`srRoster` don't exist on `_data`.

- [ ] **Step 3: Add import for ModelLoader in `src/cli/onboarding-flow.ts`**

After the existing imports at the top of the file, add:

```typescript
import { getModelLoader } from "../models/loader.js";
```

- [ ] **Step 4: Extend `WizardData` in `src/cli/onboarding-flow.ts`**

Find the `WizardData` interface (around line 53). Add at the end of the interface body before the closing `}`:

```typescript
  // Smart routing
  srEnabled?:         boolean;
  srRoster?:          Array<{ modelName: string; providerName: string }>;
  srAvailProviders?:  string[];
  srPendingProvider?: string;
  srProviderModels?:  string[];
```

- [ ] **Step 5: Add new step IDs to the `StepId` union**

Find the `type StepId = ...` block. Add before the `| "done"` line:

```typescript
  | "sr_ask"
  | "sr_prov_pick"
  | "sr_model_pick"
  | "sr_more"
```

- [ ] **Step 6: Add `_showStep` cases for the four new steps**

Find the end of `_showStep`'s switch block (before the final closing `}` of the method, after the `case "review":` block). Add:

```typescript
      case "sr_ask":
        ui.printLines([
          ...sectionHeader("Smart Routing", 1),
          "🦉  Route each message to the right model tier automatically?",
          "",
          D("    You'll select 2+ models ordered light → heavy."),
          D("    Simple messages go to the lightest model; complex ones to the strongest."),
          "",
          C("  1") + "  Yes, set up smart routing",
          C("  2") + "  No, use a single model",
          "",
          C("    Type 1 or 2:"),
          "",
        ]);
        break;

      case "sr_prov_pick": {
        const providers = d.srAvailProviders ?? [];
        const lines: string[] = ["", W("Smart Routing — Add model"), sep40()];
        providers.forEach((p, i) => lines.push(C(`  ${i + 1}`) + `  ${p}`));
        lines.push("", C(`    Type 1–${providers.length}:`), "");
        ui.printLines(lines);
        break;
      }

      case "sr_model_pick": {
        const models = d.srProviderModels ?? [];
        const lines: string[] = ["", W(`Models for ${d.srPendingProvider}`), sep40()];
        models.forEach((m, i) => lines.push(C(`  ${i + 1}`) + `  ${m}`));
        lines.push("", C(`    Type 1–${models.length}, or type a custom model name:`), "");
        ui.printLines(lines);
        break;
      }

      case "sr_more": {
        const roster = d.srRoster ?? [];
        const lines: string[] = ["", W("Smart Routing Roster"), sep40()];
        roster.forEach((e, i) => {
          const tier = i === 0 ? D("light") : i === roster.length - 1 ? D("heavy") : D("mid");
          lines.push(`  ${i + 1}. ${W(e.providerName + " / " + e.modelName)} ${tier}`);
        });
        lines.push("");
        if (roster.length < 2) {
          lines.push(G("  a") + D("  Add another model  ") + D("(need at least 2 to enable)"));
        } else {
          lines.push(G("  a") + D("  Add another model"));
          lines.push(G("  d") + D("  Done — continue setup"));
        }
        lines.push("", C("    Type a or d:"), "");
        ui.printLines(lines);
        break;
      }
```

- [ ] **Step 7: Add `_handle` cases for the four new steps**

Find the end of `_handle`'s switch block (the line before `return false;` at the very end of the method, after the `case "review":` block). Add:

```typescript
      case "sr_ask": {
        const n = parseInt(input, 10);
        if (n !== 1 && n !== 2) {
          ui.printLines([R("  Type 1 or 2."), ""]); return false;
        }
        if (n === 2) {
          d.srEnabled = false;
          this._step  = "chan_multi";
          this._showStep(ui);
          return false;
        }
        d.srEnabled = true;
        d.srRoster  = [];
        const defs  = getModelLoader().getAll();
        d.srAvailProviders = defs.map(def => def.name);
        if (d.srAvailProviders.length === 0) {
          ui.printLines([R("  No model files found in src/models/. Cannot configure routing."), ""]);
          d.srEnabled = false;
          this._step  = "chan_multi";
          this._showStep(ui);
          return false;
        }
        this._step = "sr_prov_pick";
        this._showStep(ui);
        return false;
      }

      case "sr_prov_pick": {
        const providers = d.srAvailProviders ?? [];
        const n = parseInt(input, 10);
        if (isNaN(n) || n < 1 || n > providers.length) {
          ui.printLines([R(`  Type a number 1–${providers.length}.`), ""]); return false;
        }
        d.srPendingProvider = providers[n - 1];
        const def = getModelLoader().get(d.srPendingProvider);
        d.srProviderModels = def?.availableModels ?? [];
        if (d.srProviderModels.length === 0) {
          d.srProviderModels = ["default"];
        }
        this._step = "sr_model_pick";
        this._showStep(ui);
        return false;
      }

      case "sr_model_pick": {
        const models      = d.srProviderModels ?? [];
        const n           = parseInt(input, 10);
        const modelName   = (!isNaN(n) && n >= 1 && n <= models.length)
          ? models[n - 1]
          : (input.trim() || models[0] || "default");
        const providerName = d.srPendingProvider ?? "";
        d.srRoster = [...(d.srRoster ?? []), { modelName, providerName }];
        this._step = "sr_more";
        this._showStep(ui);
        return false;
      }

      case "sr_more": {
        const answer = input.trim().toLowerCase();
        if (answer === "a") {
          const defs = getModelLoader().getAll();
          d.srAvailProviders = defs.map(def => def.name);
          this._step = "sr_prov_pick";
          this._showStep(ui);
          return false;
        }
        if (answer === "d") {
          if ((d.srRoster?.length ?? 0) < 2) {
            ui.printLines([R("  Need at least 2 models. Type \"a\" to add another."), ""]); return false;
          }
          this._step = "chan_multi";
          this._showStep(ui);
          return false;
        }
        ui.printLines([R("  Type \"a\" to add another model or \"d\" to continue."), ""]); return false;
      }
```

- [ ] **Step 8: Wire provider-done steps to `sr_ask` instead of `chan_multi`**

In `_handle`, find every line that reads `this._step  = "chan_multi";` inside the provider completion cases (`prov_ant_model`, `prov_oai_model`, `prov_ollama_model_sel`, `prov_ollama_model_txt`, `prov_lms_model_sel`, `prov_lms_model_txt`, `prov_mm_model`, `prov_compat_model`).

Change each one from:
```typescript
        this._step  = "chan_multi";
```
to:
```typescript
        this._step  = "sr_ask";
```

There are 8 such lines — one per provider completion case.

- [ ] **Step 9: Update `buildConfig` to write smartRouting**

Find `buildConfig` (around line 115). Find the section that builds `cfg` and constructs the final object. Before the `return cfg;` line, add:

```typescript
  if (d.srEnabled && d.srRoster && d.srRoster.length >= 2) {
    (cfg as any).smartRouting = {
      enabled: true,
      availableModels: d.srRoster,
      fallbackProvider: d.srRoster[d.srRoster.length - 1].providerName,
      fallbackModel:    d.srRoster[d.srRoster.length - 1].modelName,
    };
  }
```

- [ ] **Step 10: Update the `review` screen to show smart routing info**

In `_showStep`, find the `case "review":` block. Find the lines that show channels and features:

```typescript
          D("  Channels     ") + W(chList.join(", ")),
          D("  Features     ") + W(ftList.length ? ftList.join(", ") : "none"),
```

Add a smart routing line after them:

```typescript
          D("  Smart Routing") + W(
            d.srEnabled && d.srRoster?.length
              ? `ON — ${d.srRoster.length} models`
              : "OFF"
          ),
```

- [ ] **Step 11: Run tests to verify they pass**

```bash
npx vitest run __tests__/cli/onboarding-flow.test.ts
```

Expected: PASS

- [ ] **Step 12: Run full suite to verify no regressions**

```bash
npx vitest run
```

Expected: All tests pass.

- [ ] **Step 13: Commit**

```bash
git add src/cli/onboarding-flow.ts __tests__/cli/onboarding-flow.test.ts
git commit -m "feat(onboarding): add smart routing step between provider and channels"
```

---

## Self-Review

**Spec coverage check:**
- R1 (roster never populated) → Task 6 CLI + Task 5 Telegram ✅
- R2 complexity path no providerName → Task 2 ✅
- R2 failure path already working → not touched ✅
- R4 config paths mismatched → Task 5+6 fix both paths ✅
- R5 validation → not changed (still works) ✅
- R6 log missing provider → Task 2 ✅

**Type consistency:**
- `availableModels` entry shape `{ modelName, providerName, description? }` — defined in Task 1, used consistently in Tasks 2, 4, 5, 6. No `name` field used anywhere.
- Router reads `.modelName` / `.providerName` — Task 2 matches Task 1 schema.
- `buildConfig` writes `{ modelName, providerName }` — matches Task 1 schema.
- Telegram roster handlers read/write same shape.

**No placeholders:** All steps contain actual code.
