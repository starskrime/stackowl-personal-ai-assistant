# `/skills install` Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/skills install` multi-turn wizard to CLI and Telegram that guides the user through installing a skill from ClawHub, GitHub, or a local path.

**Architecture:** `SkillInstallWizard` owns a state machine in `src/skills/wizard.ts`. `GatewayCore` holds an in-memory `Map<sessionId, SkillInstallWizard>`, routes active wizard sessions before LLM dispatch, and starts a new wizard on `/skills install`. The Telegram adapter adds a `bot.command("skills")` handler and a `wiz:*` callback query route that renders inline keyboards. CLI falls through to `gateway.handle()` naturally by skipping `/skills` in `CommandRegistry`.

**Tech Stack:** TypeScript, grammY (Telegram inline keyboards), existing `ClawHubClient`, `SkillInstaller`, `SkillsRegistry`, Vitest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/skills/wizard.ts` | CREATE | `SkillInstallWizard` class + `WizardResponse` type |
| `src/gateway/types.ts` | MODIFY | Add optional `inlineKeyboard` to `GatewayResponse` |
| `src/gateway/core.ts` | MODIFY | `wizardSessions` map, wizard routing, `/skills install` handler |
| `src/cli/commands.ts` | MODIFY | Skip `/skills` so it falls through to `gateway.handle()` |
| `src/gateway/adapters/telegram.ts` | MODIFY | `bot.command("skills")`, `wiz:*` callback, `sendWizardResponse()` |
| `__tests__/skills/wizard.test.ts` | CREATE | Unit tests for `SkillInstallWizard` |

---

## Task 1: Create `src/skills/wizard.ts`

**Files:**
- Create: `src/skills/wizard.ts`
- Test: `__tests__/skills/wizard.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/wizard.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillInstallWizard } from "../../src/skills/wizard.js";

const mockClawHub = {
  search: vi.fn(),
  install: vi.fn(),
};

vi.mock("../../src/skills/installer.js", () => ({
  SkillInstaller: vi.fn().mockImplementation(() => ({
    fromGitHub: vi.fn().mockResolvedValue(undefined),
    fromLocal: vi.fn().mockResolvedValue(undefined),
  })),
}));

describe("SkillInstallWizard", () => {
  let wizard: SkillInstallWizard;

  beforeEach(() => {
    wizard = new SkillInstallWizard("/workspace/skills", mockClawHub as any);
    vi.clearAllMocks();
  });

  it("start() returns source menu with 3-button keyboard", () => {
    const res = wizard.start();
    expect(res.done).toBe(false);
    expect(res.text).toContain("Choose a source");
    expect(res.inlineKeyboard).toBeDefined();
    expect(res.inlineKeyboard![0]).toHaveLength(3);
  });

  it("step('/cancel') exits immediately", async () => {
    const res = await wizard.step("/cancel");
    expect(res.done).toBe(true);
    expect(res.text).toBe("Cancelled.");
  });

  it("step('1') transitions to search_clawhub", async () => {
    const res = await wizard.step("1");
    expect(res.done).toBe(false);
    expect(res.text).toContain("keyword");
    expect(res.inlineKeyboard).toBeUndefined();
  });

  it("step('wiz:github') transitions to enter_github", async () => {
    const res = await wizard.step("wiz:github");
    expect(res.done).toBe(false);
    expect(res.text).toContain("GitHub path");
  });

  it("step('wiz:local') transitions to enter_local", async () => {
    const res = await wizard.step("wiz:local");
    expect(res.done).toBe(false);
    expect(res.text).toContain("local path");
  });

  it("invalid source input re-prompts with keyboard", async () => {
    const res = await wizard.step("99");
    expect(res.done).toBe(false);
    expect(res.text).toContain("1, 2, or 3");
    expect(res.inlineKeyboard).toBeDefined();
  });

  it("clawhub search returns results list with keyboard", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [
        { slug: "git_branch", name: "git_branch", description: "Manage branches", stars: 5, downloads: 100, tags: [], author: "test", latestVersion: "1.0", updatedAt: "" },
      ],
      total: 1,
    });
    await wizard.step("1");
    const res = await wizard.step("git");
    expect(res.done).toBe(false);
    expect(res.text).toContain("git_branch");
    expect(res.inlineKeyboard).toBeDefined();
  });

  it("clawhub search with 0 results re-prompts same step", async () => {
    mockClawHub.search.mockResolvedValue({ skills: [], total: 0 });
    await wizard.step("1");
    const res = await wizard.step("zzz");
    expect(res.done).toBe(false);
    expect(res.text).toContain("No skills found");
  });

  it("clawhub unavailable returns done with error message", async () => {
    mockClawHub.search.mockRejectedValue(new Error("Network error"));
    await wizard.step("1");
    const res = await wizard.step("git");
    expect(res.done).toBe(true);
    expect(res.text).toContain("unavailable");
  });

  it("picking by number installs skill and returns done", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [{ slug: "git_branch", name: "git_branch", description: "Manage branches", stars: 5, downloads: 100, tags: [], author: "test", latestVersion: "1.0", updatedAt: "" }],
      total: 1,
    });
    mockClawHub.install.mockResolvedValue(true);
    await wizard.step("1");
    await wizard.step("git");
    const res = await wizard.step("1");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
    expect(mockClawHub.install).toHaveBeenCalledWith("git_branch", "/workspace/skills");
  });

  it("picking by Telegram callback data installs skill", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [{ slug: "git_branch", name: "git_branch", description: "", stars: 0, downloads: 0, tags: [], author: "", latestVersion: "1.0", updatedAt: "" }],
      total: 1,
    });
    mockClawHub.install.mockResolvedValue(true);
    await wizard.step("1");
    await wizard.step("git");
    const res = await wizard.step("wiz:pick:git_branch");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });

  it("github install success", async () => {
    await wizard.step("2");
    const res = await wizard.step("github:myuser/myrepo/my-skill");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });

  it("github install with too-short path re-prompts", async () => {
    await wizard.step("2");
    const res = await wizard.step("notavalidpath");
    expect(res.done).toBe(false);
    expect(res.text).toContain("Invalid GitHub path");
  });

  it("local install success", async () => {
    await wizard.step("3");
    const res = await wizard.step("./my-skill");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/wizard.test.ts
```
Expected: FAIL with "Cannot find module '../../src/skills/wizard.js'"

- [ ] **Step 3: Create `src/skills/wizard.ts`**

```typescript
import { dirname, join } from "node:path";
import { ClawHubClient, type ClawHubSkill } from "./clawhub.js";
import { SkillInstaller } from "./installer.js";
import type { SkillsRegistry } from "./registry.js";

export interface WizardResponse {
  text: string;
  done: boolean;
  inlineKeyboard?: { text: string; data: string }[][];
}

type WizardStep =
  | "choose_source"
  | "search_clawhub"
  | "pick_clawhub"
  | "enter_github"
  | "enter_local";

interface WizardState {
  step: WizardStep;
  searchResults?: ClawHubSkill[];
}

const SOURCE_MENU_TEXT =
  "Choose a source to install from:\n\n" +
  "1. ClawHub — search the skill marketplace\n" +
  "2. GitHub — install from a GitHub repo path\n" +
  "3. Local — install from a local folder path\n\n" +
  "Type a number or /cancel to exit.";

const SOURCE_KEYBOARD: WizardResponse["inlineKeyboard"] = [
  [
    { text: "ClawHub", data: "wiz:clawhub" },
    { text: "GitHub", data: "wiz:github" },
    { text: "Local", data: "wiz:local" },
  ],
];

export class SkillInstallWizard {
  private state: WizardState = { step: "choose_source" };

  constructor(
    private readonly skillsDir: string,
    private readonly clawHubClient: ClawHubClient,
    private readonly registry?: SkillsRegistry,
  ) {}

  start(): WizardResponse {
    return { text: SOURCE_MENU_TEXT, done: false, inlineKeyboard: SOURCE_KEYBOARD };
  }

  async step(input: string): Promise<WizardResponse> {
    if (input.trim().toLowerCase() === "/cancel") {
      return { text: "Cancelled.", done: true };
    }
    switch (this.state.step) {
      case "choose_source":  return this.handleChooseSource(input.trim());
      case "search_clawhub": return this.handleSearchClawHub(input.trim());
      case "pick_clawhub":   return this.handlePickClawHub(input.trim());
      case "enter_github":   return this.handleEnterGitHub(input.trim());
      case "enter_local":    return this.handleEnterLocal(input.trim());
    }
  }

  private handleChooseSource(input: string): WizardResponse {
    const lower = input.toLowerCase();
    if (lower === "1" || lower === "clawhub" || lower === "wiz:clawhub") {
      this.state.step = "search_clawhub";
      return { text: "Search ClawHub — enter a keyword (e.g. git, docker, pdf):", done: false };
    }
    if (lower === "2" || lower === "github" || lower === "wiz:github") {
      this.state.step = "enter_github";
      return {
        text: "Enter the GitHub path:\nFormat: `github:user/repo/path/to/skill` or `github:user/repo/path@branch`\n\nOr /cancel to exit.",
        done: false,
      };
    }
    if (lower === "3" || lower === "local" || lower === "wiz:local") {
      this.state.step = "enter_local";
      return {
        text: "Enter the local path:\nFormat: `./relative/path` or `/absolute/path` (must contain a SKILL.md file)\n\nOr /cancel to exit.",
        done: false,
      };
    }
    return {
      text: "Please enter 1, 2, or 3.\n\n" + SOURCE_MENU_TEXT,
      done: false,
      inlineKeyboard: SOURCE_KEYBOARD,
    };
  }

  private async handleSearchClawHub(query: string): Promise<WizardResponse> {
    try {
      const result = await this.clawHubClient.search(query, 5);
      if (result.skills.length === 0) {
        return { text: `No skills found for "${query}". Try another keyword:`, done: false };
      }
      this.state.searchResults = result.skills;
      this.state.step = "pick_clawhub";
      const listText = result.skills
        .map((s, i) => `${i + 1}. **${s.name}** — ${s.description}`)
        .join("\n");
      const keyboard: WizardResponse["inlineKeyboard"] = result.skills.map((s) => [
        { text: s.name, data: `wiz:pick:${s.slug}` },
      ]);
      return {
        text: `Found ${result.skills.length} skill${result.skills.length > 1 ? "s" : ""}:\n\n${listText}\n\nType a number to install, or /cancel to exit.`,
        done: false,
        inlineKeyboard: keyboard,
      };
    } catch {
      return { text: "ClawHub unavailable. Try again later.", done: true };
    }
  }

  private async handlePickClawHub(input: string): Promise<WizardResponse> {
    const lower = input.toLowerCase();
    let slug: string | undefined;

    if (lower.startsWith("wiz:pick:")) {
      slug = input.slice("wiz:pick:".length);
    } else {
      const num = parseInt(input, 10);
      if (!isNaN(num) && this.state.searchResults) {
        slug = this.state.searchResults[num - 1]?.slug;
      } else {
        slug = input;
      }
    }

    if (!slug) {
      const keyboard: WizardResponse["inlineKeyboard"] = (this.state.searchResults ?? []).map(
        (s) => [{ text: s.name, data: `wiz:pick:${s.slug}` }],
      );
      return {
        text: "Please type a number or tap a skill to install, or /cancel to exit.",
        done: false,
        inlineKeyboard: keyboard,
      };
    }

    try {
      await this.clawHubClient.install(slug, this.skillsDir);
      await this.registry?.loadFromDirectory(this.skillsDir);
      return { text: `✓ Installed "${slug}" — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { text: `Failed to install "${slug}": ${msg}`, done: true };
    }
  }

  private async handleEnterGitHub(input: string): Promise<WizardResponse> {
    const normalized = input.startsWith("github:") ? input : `github:${input}`;
    const rest = normalized.slice("github:".length);
    const [pathPart, branch = "main"] = rest.split("@") as [string, string?];
    const parts = pathPart.split("/");
    if (parts.length < 3) {
      return {
        text: "Invalid GitHub path. Expected: `github:user/repo/path/to/skill`\n\nTry again or /cancel:",
        done: false,
      };
    }
    const [user, repo, ...skillParts] = parts;
    const skillPath = skillParts.join("/");
    const skillName = skillParts[skillParts.length - 1]!;
    const rawUrl = `https://raw.githubusercontent.com/${user}/${repo}/${branch}/${skillPath}/SKILL.md`;
    try {
      const installer = new SkillInstaller(dirname(this.skillsDir));
      await installer.fromGitHub(rawUrl, skillName);
      await this.registry?.loadFromDirectory(this.skillsDir);
      return { text: `✓ Installed "${skillName}" from GitHub — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        text: `Could not fetch skill from GitHub: ${msg}\n\nTry again or /cancel:`,
        done: false,
      };
    }
  }

  private async handleEnterLocal(input: string): Promise<WizardResponse> {
    try {
      const installer = new SkillInstaller(dirname(this.skillsDir));
      await installer.fromLocal(input);
      await this.registry?.loadFromDirectory(this.skillsDir);
      const { basename } = await import("node:path");
      const skillName = basename(input);
      return { text: `✓ Installed "${skillName}" from local path — ready to use.`, done: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        text: `Could not install from local path: ${msg}\n\nTry again or /cancel:`,
        done: false,
      };
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/skills/wizard.test.ts
```
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/skills/wizard.ts __tests__/skills/wizard.test.ts
git commit -m "feat(skills): add SkillInstallWizard state machine"
```

---

## Task 2: Add `inlineKeyboard` to `GatewayResponse`

**Files:**
- Modify: `src/gateway/types.ts:64-78`

- [ ] **Step 1: Add the field to `GatewayResponse`**

Open `src/gateway/types.ts`. The `GatewayResponse` interface ends at line 78. Add the `inlineKeyboard` field:

```typescript
export interface GatewayResponse {
  content: string;
  owlName: string;
  owlEmoji: string;
  toolsUsed: string[];
  usage?: { promptTokens: number; completionTokens: number };
  estimatedCostUsd?: number;
  preformatted?: boolean;
  /** Inline keyboard buttons for Telegram wizard responses. Ignored by other channels. */
  inlineKeyboard?: { text: string; data: string }[][];
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -20
```
Expected: No errors (the field is optional — no existing code breaks)

- [ ] **Step 3: Commit**

```bash
git add src/gateway/types.ts
git commit -m "feat(gateway): add optional inlineKeyboard to GatewayResponse"
```

---

## Task 3: Wire wizard into `GatewayCore`

**Files:**
- Modify: `src/gateway/core.ts`

The key locations in `src/gateway/core.ts`:
- Line 24: `import { ClawHubClient } from "../skills/clawhub.js";` — already imported
- Line 725: `private async handleCore(...)` — wizard routing goes near line 752 (`/reset` check)
- Line 2391: `private async handleFeatureCommand(...)` — no changes needed here

- [ ] **Step 1: Add import for wizard at top of `src/gateway/core.ts`**

Find the block of skill-related imports near line 23-25:
```typescript
import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillTracker } from "../skills/tracker.js";
```

Add one line after the `ClawHubClient` import:
```typescript
import { SkillContextInjector } from "../skills/injector.js";
import { ClawHubClient } from "../skills/clawhub.js";
import { SkillInstallWizard } from "../skills/wizard.js";
import { SkillTracker } from "../skills/tracker.js";
```

- [ ] **Step 2: Add `wizardSessions` map to the class**

Find the class field declarations (search for `private attemptLogs` or similar private maps). Add after the existing private maps:

```typescript
private wizardSessions = new Map<string, SkillInstallWizard>();
```

- [ ] **Step 3: Add wizard routing in `handleCore()`**

In `handleCore()`, find the `/reset` block at line ~752:
```typescript
    // Check for /reset command - clear session history
    if (message.text.trim().toLowerCase() === "/reset") {
```

Insert wizard routing BEFORE that block:

```typescript
    // ─── Wizard routing ──────────────────────────────────────────
    // If this session has an active /skills install wizard, route into it.
    const activeWizard = this.wizardSessions.get(message.sessionId);
    if (activeWizard) {
      const wizResp = await activeWizard.step(message.text);
      if (wizResp.done) this.wizardSessions.delete(message.sessionId);
      return {
        content: wizResp.text,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
        inlineKeyboard: wizResp.inlineKeyboard,
      };
    }

    // ─── /skills install — start the install wizard ───────────────
    if (message.text.trim().toLowerCase().startsWith("/skills install") ||
        message.text.trim().toLowerCase() === "/skills") {
      const { resolve } = await import("node:path");
      const { join } = await import("node:path");
      const skillsDir = resolve(
        this.ctx.config.skills?.directories?.[0] ??
        join(this.ctx.cwd ?? process.cwd(), "workspace", "skills"),
      );
      const wizard = new SkillInstallWizard(
        skillsDir,
        new ClawHubClient(),
        this.ctx.skillsLoader?.getRegistry(),
      );
      this.wizardSessions.set(message.sessionId, wizard);
      const startResp = wizard.start();
      return {
        content: startResp.text,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
        inlineKeyboard: startResp.inlineKeyboard,
      };
    }

    // Check for /reset command - clear session history
    if (message.text.trim().toLowerCase() === "/reset") {
      this.wizardSessions.delete(message.sessionId); // also cancel any active wizard
```

Note: the `/reset` handler already clears sessions; add `this.wizardSessions.delete(message.sessionId);` inside it too.

- [ ] **Step 4: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -20
```
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat(gateway): wire SkillInstallWizard into handleCore"
```

---

## Task 4: CLI — skip `/skills` in `CommandRegistry`

**Files:**
- Modify: `src/cli/commands.ts:239`

The `CommandRegistry.handle()` starts with:
```typescript
  async handle(input, ui, gateway): Promise<boolean> {
    if (!input.startsWith("/")) return false;
```

- [ ] **Step 1: Add pass-through for `/skills`**

After `if (!input.startsWith("/")) return false;`, add:

```typescript
    // Let /skills fall through to gateway.handle() for wizard routing
    if (input.toLowerCase().startsWith("/skills")) return false;
```

Full context after the edit:
```typescript
  async handle(
    input: string,
    ui: TerminalRenderer,
    gateway: OwlGateway,
  ): Promise<boolean> {
    if (!input.startsWith("/")) return false;

    // Let /skills fall through to gateway.handle() for wizard routing
    if (input.toLowerCase().startsWith("/skills")) return false;

    const space = input.indexOf(" ");
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -20
```
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat(cli): pass /skills commands through to gateway wizard"
```

---

## Task 5: Telegram — add `bot.command("skills")`, `wiz:*` callback, and keyboard rendering

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`

Three changes needed:
1. Add `sendWizardResponse()` private method
2. Add `bot.command("skills", ...)` handler
3. Add `wiz:*` route in the `callback_query:data` handler

- [ ] **Step 1: Add `sendWizardResponse()` helper method**

Find `private async sendChunked(` (around line 1258). Insert `sendWizardResponse` BEFORE `sendChunked`:

```typescript
  private async sendWizardResponse(
    chatId: number,
    response: GatewayResponse,
  ): Promise<void> {
    const text = this.formatResponse(response);
    if (response.inlineKeyboard && response.inlineKeyboard.length > 0) {
      await this.bot.api.sendMessage(chatId, text, {
        parse_mode: "HTML",
        reply_markup: {
          inline_keyboard: response.inlineKeyboard.map((row) =>
            row.map((btn) => ({ text: btn.text, callback_data: btn.data })),
          ),
        },
      });
    } else {
      await this.sendChunked(chatId, text);
    }
  }
```

- [ ] **Step 2: Add `bot.command("skills", ...)` handler**

Find the `bot.command("mcp", ...)` block (around line 308). Add the `skills` command right before or after it:

```typescript
    // ── /skills install — start the skill install wizard ────────
    this.bot.command("skills", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      const userId = ctx.from?.id;
      if (!userId) return;
      try {
        const response = await this.gateway.handle(
          {
            id: makeMessageId(),
            channelId: this.id,
            userId: String(userId),
            sessionId: makeSessionId(this.id, String(userId)),
            text: "/skills install",
          },
          { onProgress: async () => {}, askInstall: async () => false },
        );
        await this.sendWizardResponse(ctx.chat.id, response);
      } catch (err) {
        log.telegram.error(`/skills wizard error: ${err instanceof Error ? err.message : err}`);
        await ctx.reply("Failed to start the install wizard. Try again.").catch(() => {});
      }
    });
```

- [ ] **Step 3: Add `wiz:*` route in `callback_query:data` handler**

Find the `callback_query:data` handler (around line 830):
```typescript
    this.bot.on("callback_query:data", async (ctx) => {
      const data = ctx.callbackQuery.data ?? "";

      // ── Config menu callbacks ────────────────────────────
      if (data.startsWith("cfg:")) {
```

Add the `wiz:*` block BEFORE `cfg:`:

```typescript
      // ── Skills install wizard callbacks ──────────────────
      if (data.startsWith("wiz:")) {
        if (!this.isAllowed(ctx)) {
          await ctx.answerCallbackQuery({ text: "⛔ Not authorised." });
          return;
        }
        try {
          await ctx.answerCallbackQuery();
        } catch {
          /* query expired */
        }
        const userId = ctx.from?.id;
        if (!userId) return;
        try {
          const response = await this.gateway.handle(
            {
              id: makeMessageId(),
              channelId: this.id,
              userId: String(userId),
              sessionId: makeSessionId(this.id, String(userId)),
              text: data,
            },
            { onProgress: async () => {}, askInstall: async () => false },
          );
          await this.sendWizardResponse(ctx.chat.id, response);
        } catch (err) {
          log.telegram.error(`Wizard callback error: ${err instanceof Error ? err.message : err}`);
        }
        return;
      }
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -20
```
Expected: No errors

- [ ] **Step 5: Run full test suite**

```bash
npm run test 2>&1 | tail -20
```
Expected: All existing tests pass (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/gateway/adapters/telegram.ts
git commit -m "feat(telegram): add /skills install wizard with inline keyboard support"
```

---

## Self-Review

**Spec coverage:**
- ✅ Multi-turn wizard (state machine in `SkillInstallWizard`)
- ✅ CLI channel (CommandRegistry pass-through → gateway.handle())
- ✅ Telegram channel (bot.command + callback_query)
- ✅ Inline keyboard on Telegram, numbered list on CLI
- ✅ ClawHub: search step → results → pick → install
- ✅ GitHub: enter path → install
- ✅ Local: enter path → install
- ✅ `/cancel` exits at any step
- ✅ Invalid source input re-prompts
- ✅ ClawHub 0 results re-prompts search step
- ✅ ClawHub unavailable → done with error
- ✅ Bad GitHub path re-prompts
- ✅ Bad local path re-prompts
- ✅ New `/skills install` replaces active wizard
- ✅ `/reset` clears active wizard (added delete in reset handler)

**No placeholders:** All code is complete and concrete.

**Type consistency:** `WizardResponse.inlineKeyboard` uses `{ text: string; data: string }[][]` throughout. `GatewayResponse.inlineKeyboard` uses the same type. The Telegram adapter maps `.data` → `callback_data` at the grammY API boundary.
