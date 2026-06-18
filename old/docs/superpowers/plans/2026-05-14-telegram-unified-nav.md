# Telegram Unified Navigation — Bot Menu + Persistent Keyboard + Inline Control Panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Telegram user instant, tap-only access to all StackOwl platform features through three complementary layers: Telegram's native command list (bottom-left menu button), a persistent Reply Keyboard always visible above the text input, and a full inline control panel reachable via `/menu`.

**Architecture:** A new `telegram-menu/` directory holds the unified nav system (`nav-state.ts` for per-user navigation stacks, `screens.ts` for pure screen renderers, `root-menu.ts` as the controller). `TelegramRootMenu` handles all `nav:*` callbacks and delegates to the existing `TelegramConfigMenu` and `TelegramVoiceMenu` for their sub-flows. The existing flat commands (`/mcp`, `/memory`, `/owl`, etc.) are untouched — the nav system calls the same underlying routers.

**Tech Stack:** grammY `InlineKeyboard`, `Keyboard` (Reply Keyboard), `bot.api.setMyCommands`, `bot.api.setChatMenuButton`. TypeScript. Vitest for tests.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/gateway/adapters/telegram-menu/nav-state.ts` | Per-user navigation stack (screen history, message ID being edited, pending text input) |
| Create | `src/gateway/adapters/telegram-menu/screens.ts` | Pure screen renderers returning `ScreenContent { text; keyboard }` |
| Create | `src/gateway/adapters/telegram-menu/root-menu.ts` | Controller: `handleCommand`, `handleCallback`, `handleTextInput` |
| Create | `src/gateway/adapters/telegram-menu/index.ts` | Re-exports |
| Create | `__tests__/gateway/adapters/telegram-menu.test.ts` | Unit + integration tests |
| Modify | `src/gateway/adapters/telegram.ts` | Wire root menu, persistent keyboard, `nav:*` callbacks, `setMyCommands` |

---

## Task 1: Navigation State Manager

**Files:**
- Create: `src/gateway/adapters/telegram-menu/nav-state.ts`

- [ ] **Step 1: Write the failing test**

File: `__tests__/gateway/adapters/telegram-menu.test.ts`

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { NavStateManager } from "../../../src/gateway/adapters/telegram-menu/nav-state.js";

describe("NavStateManager", () => {
  let mgr: NavStateManager;

  beforeEach(() => { mgr = new NavStateManager(); });

  it("returns undefined for unknown user", () => {
    expect(mgr.get(99)).toBeUndefined();
  });

  it("opens a session at root screen", () => {
    mgr.open(1, 100, 42);
    const s = mgr.get(1)!;
    expect(s.chatId).toBe(100);
    expect(s.messageId).toBe(42);
    expect(s.stack).toEqual(["root"]);
  });

  it("push navigates and records history", () => {
    mgr.open(1, 100, 42);
    mgr.push(1, "mcp");
    const s = mgr.get(1)!;
    expect(s.stack).toEqual(["root", "mcp"]);
  });

  it("pop goes back one screen", () => {
    mgr.open(1, 100, 42);
    mgr.push(1, "mcp");
    mgr.pop(1);
    expect(mgr.get(1)!.stack).toEqual(["root"]);
  });

  it("pop does not go below root", () => {
    mgr.open(1, 100, 42);
    mgr.pop(1);
    expect(mgr.get(1)!.stack).toEqual(["root"]);
  });

  it("current() returns last screen name", () => {
    mgr.open(1, 100, 42);
    mgr.push(1, "owl");
    expect(mgr.current(1)).toBe("owl");
  });

  it("setPendingText and clearPendingText", () => {
    mgr.open(1, 100, 42);
    mgr.setPendingText(1, "mcp:add");
    expect(mgr.get(1)!.pendingText).toBe("mcp:add");
    mgr.clearPendingText(1);
    expect(mgr.get(1)!.pendingText).toBeUndefined();
  });

  it("evicts stale sessions on evict()", () => {
    mgr.open(1, 100, 42);
    const s = mgr.get(1)!;
    s.lastActivity = Date.now() - 11 * 60 * 1000; // 11 minutes ago
    mgr.evict();
    expect(mgr.get(1)).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | head -20
```

Expected: FAIL with `Cannot find module '.../nav-state.js'`

- [ ] **Step 3: Implement nav-state.ts**

```typescript
/**
 * StackOwl — Telegram Unified Nav: Navigation State Manager
 *
 * Per-user nav stack (screen history + message being edited in-place).
 * Pattern mirrors telegram-config/state.ts (MenuStateManager).
 */

const NAV_TTL_MS = 10 * 60 * 1000; // 10 minutes inactivity

export interface NavState {
  userId: number;
  chatId: number;
  /** The single Telegram message ID being edited in-place */
  messageId: number;
  /** Screen stack — last entry is current screen */
  stack: string[];
  /** When set: next plain-text message from user is consumed by this action */
  pendingText?: string;
  lastActivity: number;
}

export class NavStateManager {
  private states = new Map<number, NavState>();
  private cleanupInterval: ReturnType<typeof setInterval>;

  constructor() {
    this.cleanupInterval = setInterval(() => this.evict(), 5 * 60 * 1000);
    this.cleanupInterval.unref();
  }

  get(userId: number): NavState | undefined {
    return this.states.get(userId);
  }

  /** Create or reset a nav session at the root screen */
  open(userId: number, chatId: number, messageId: number): NavState {
    const state: NavState = { userId, chatId, messageId, stack: ["root"], lastActivity: Date.now() };
    this.states.set(userId, state);
    return state;
  }

  /** Navigate to a new screen (push) */
  push(userId: number, screen: string): NavState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    s.stack.push(screen);
    s.lastActivity = Date.now();
    s.pendingText = undefined;
    return s;
  }

  /** Go back one screen (pop) — never goes below root */
  pop(userId: number): NavState | undefined {
    const s = this.states.get(userId);
    if (!s) return undefined;
    if (s.stack.length > 1) s.stack.pop();
    s.lastActivity = Date.now();
    s.pendingText = undefined;
    return s;
  }

  /** Return current screen name */
  current(userId: number): string | undefined {
    const s = this.states.get(userId);
    return s?.stack[s.stack.length - 1];
  }

  /** Update the message ID being edited (e.g. after sending a new message) */
  setMessageId(userId: number, messageId: number): void {
    const s = this.states.get(userId);
    if (s) { s.messageId = messageId; s.lastActivity = Date.now(); }
  }

  setPendingText(userId: number, action: string): void {
    const s = this.states.get(userId);
    if (s) { s.pendingText = action; s.lastActivity = Date.now(); }
  }

  clearPendingText(userId: number): void {
    const s = this.states.get(userId);
    if (s) { s.pendingText = undefined; }
  }

  touch(userId: number): void {
    const s = this.states.get(userId);
    if (s) s.lastActivity = Date.now();
  }

  delete(userId: number): void {
    this.states.delete(userId);
  }

  evict(): void {
    const now = Date.now();
    for (const [uid, s] of this.states) {
      if (now - s.lastActivity > NAV_TTL_MS) this.states.delete(uid);
    }
  }

  destroy(): void {
    clearInterval(this.cleanupInterval);
    this.states.clear();
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | grep -E "pass|fail|PASS|FAIL"
```

Expected: All 8 NavStateManager tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/adapters/telegram-menu/nav-state.ts __tests__/gateway/adapters/telegram-menu.test.ts
git commit -m "feat(telegram-menu): nav state manager with push/pop/pendingText"
```

---

## Task 2: Screen Renderers

**Files:**
- Create: `src/gateway/adapters/telegram-menu/screens.ts`

The renderers are pure functions: data in → `ScreenContent` out. No side effects, no async.

- [ ] **Step 1: Write failing tests** (add to existing test file)

Append to `__tests__/gateway/adapters/telegram-menu.test.ts`:

```typescript
import { InlineKeyboard } from "grammy";
import {
  renderRoot,
  renderStatus,
  renderMcpList,
  renderOwlList,
  renderMemoryInfo,
  renderSkillsList,
  type ScreenContent,
} from "../../../src/gateway/adapters/telegram-menu/screens.js";

describe("screens.ts — renderRoot", () => {
  it("returns HTML text and 8 buttons arranged in rows", () => {
    const sc = renderRoot();
    expect(sc.text).toContain("Control Panel");
    const kb = sc.keyboard.inline_keyboard;
    // At least 3 rows of buttons
    expect(kb.length).toBeGreaterThanOrEqual(3);
    // Flatten all buttons and find Menu items
    const allButtons = kb.flat().map(b => b.callback_data);
    expect(allButtons).toContain("nav:cfg");
    expect(allButtons).toContain("nav:vc");
    expect(allButtons).toContain("nav:mcp");
    expect(allButtons).toContain("nav:owl");
    expect(allButtons).toContain("nav:mem");
    expect(allButtons).toContain("nav:sk");
    expect(allButtons).toContain("nav:st");
  });
});

describe("screens.ts — renderStatus", () => {
  it("includes model and owl name", () => {
    const sc = renderStatus("claude-sonnet-4-6", "🦉", "Aria");
    expect(sc.text).toContain("claude-sonnet-4-6");
    expect(sc.text).toContain("Aria");
  });

  it("has a Back button with nav:bk", () => {
    const sc = renderStatus("m", "🦉", "X");
    const allButtons = sc.keyboard.inline_keyboard.flat().map(b => b.callback_data);
    expect(allButtons).toContain("nav:bk");
  });
});

describe("screens.ts — renderMcpList", () => {
  it("shows connected and disconnected servers", () => {
    const servers = [
      { name: "filesystem", connected: true, toolCount: 5 },
      { name: "git", connected: false, toolCount: 0 },
    ];
    const sc = renderMcpList(servers);
    expect(sc.text).toContain("filesystem");
    expect(sc.text).toContain("git");
    const cbData = sc.keyboard.inline_keyboard.flat().map(b => b.callback_data);
    expect(cbData.some(d => d.includes("nav:mcp:en:") || d.includes("nav:mcp:dis:"))).toBe(true);
  });

  it("shows empty state message", () => {
    const sc = renderMcpList([]);
    expect(sc.text).toContain("No MCP");
  });
});

describe("screens.ts — renderOwlList", () => {
  it("marks current owl and provides switch buttons", () => {
    const owls = [{ name: "Aria", emoji: "🦉", isPinned: true }, { name: "Max", emoji: "🤖", isPinned: false }];
    const sc = renderOwlList(owls, "Aria");
    expect(sc.text).toContain("Aria");
    const cbData = sc.keyboard.inline_keyboard.flat().map(b => b.callback_data);
    // Switch button for non-current owl
    expect(cbData).toContain("nav:owl:sw:Max");
    // No switch for current owl
    expect(cbData).not.toContain("nav:owl:sw:Aria");
  });
});

describe("screens.ts — renderSkillsList", () => {
  it("shows skill names and enable/disable buttons", () => {
    const skills = [
      { name: "remember", enabled: true },
      { name: "web-search", enabled: false },
    ];
    const sc = renderSkillsList(skills);
    const cbData = sc.keyboard.inline_keyboard.flat().map(b => b.callback_data);
    expect(cbData).toContain("nav:sk:dis:remember");
    expect(cbData).toContain("nav:sk:en:web-search");
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | grep -E "FAIL|Cannot find"
```

Expected: FAIL — `Cannot find module '.../screens.js'`

- [ ] **Step 3: Implement screens.ts**

```typescript
/**
 * StackOwl — Telegram Unified Nav: Screen Renderers
 *
 * Pure functions: data in → ScreenContent out.
 * No side effects, no async, no imports from gateway.
 *
 * Callback prefix: "nav:" (abbreviated to stay within 64-byte limit).
 *   nav:r   = root (unused as callback — just for state tracking)
 *   nav:st  = status
 *   nav:cfg = AI config (delegates to TelegramConfigMenu)
 *   nav:vc  = voice (delegates to TelegramVoiceMenu)
 *   nav:mcp = MCP server list
 *   nav:mcp:dis:{name} = disable MCP server
 *   nav:mcp:en:{name}  = enable MCP server
 *   nav:mcp:rc:{name}  = reconnect MCP server
 *   nav:owl = owl list
 *   nav:owl:sw:{name}  = switch to owl
 *   nav:mem = memory info
 *   nav:sk  = skills list
 *   nav:sk:en:{name}   = enable skill
 *   nav:sk:dis:{name}  = disable skill
 *   nav:bk  = go back
 */

import { InlineKeyboard } from "grammy";

export interface ScreenContent {
  text: string;
  keyboard: InlineKeyboard;
}

// ─── Helpers ──────────────────────────────────────────────────────

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Truncate a string to fit within a callback_data byte limit when prefixed */
function truncKey(s: string, maxBytes = 40): string {
  const enc = new TextEncoder();
  if (enc.encode(s).length <= maxBytes) return s;
  // Truncate by character until it fits
  let t = s;
  while (enc.encode(t).length > maxBytes && t.length > 0) t = t.slice(0, -1);
  return t;
}

// ─── Root screen ──────────────────────────────────────────────────

export function renderRoot(): ScreenContent {
  const text =
    `🦉 <b>StackOwl Control Panel</b>\n\n` +
    `Tap any section to manage it.`;

  const keyboard = new InlineKeyboard()
    .text("🤖 AI Config", "nav:cfg").text("🎤 Voice", "nav:vc").row()
    .text("🔌 MCP Servers", "nav:mcp").text("🧠 Memory", "nav:mem").row()
    .text("🦉 Owls", "nav:owl").text("🔧 Skills", "nav:sk").row()
    .text("📊 Status", "nav:st");

  return { text, keyboard };
}

// ─── Status screen ────────────────────────────────────────────────

export function renderStatus(
  model: string,
  owlEmoji: string,
  owlName: string,
  sessionCount = 0,
): ScreenContent {
  const text =
    `📊 <b>Status</b>\n\n` +
    `<b>Model:</b> <code>${esc(model)}</code>\n` +
    `<b>Owl:</b> ${esc(owlEmoji)} ${esc(owlName)}\n` +
    `<b>Active sessions:</b> ${sessionCount}`;

  const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
  return { text, keyboard };
}

// ─── MCP screens ──────────────────────────────────────────────────

export interface McpServerInfo {
  name: string;
  connected: boolean;
  toolCount: number;
}

export function renderMcpList(servers: McpServerInfo[]): ScreenContent {
  if (servers.length === 0) {
    const text = `🔌 <b>MCP Servers</b>\n\nNo MCP servers configured.\n\nUse <code>/mcp add &lt;package&gt;</code> to add one.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const lines = servers.map(s =>
    `${s.connected ? "🟢" : "🔴"} <b>${esc(s.name)}</b> (${s.toolCount} tools)`
  );
  const text = `🔌 <b>MCP Servers</b>\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const s of servers) {
    const key = truncKey(s.name);
    if (s.connected) {
      keyboard.text(`⏸ ${s.name}`, `nav:mcp:dis:${key}`).text(`🔄 ${s.name}`, `nav:mcp:rc:${key}`).row();
    } else {
      keyboard.text(`▶️ ${s.name}`, `nav:mcp:en:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}

// ─── Owl screens ──────────────────────────────────────────────────

export interface OwlInfo {
  name: string;
  emoji: string;
  isPinned: boolean;
}

export function renderOwlList(owls: OwlInfo[], currentOwlName: string): ScreenContent {
  if (owls.length === 0) {
    const text = `🦉 <b>Owls</b>\n\nNo custom owls found.\n\nUse <code>/owl create</code> to create one.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const lines = owls.map(o => {
    const active = o.name === currentOwlName ? " ✅" : "";
    return `${o.emoji} <b>${esc(o.name)}</b>${active}`;
  });
  const text = `🦉 <b>Owls</b>\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const o of owls) {
    if (o.name !== currentOwlName) {
      const key = truncKey(o.name);
      keyboard.text(`Switch → ${o.emoji} ${o.name}`, `nav:owl:sw:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}

// ─── Memory screen ────────────────────────────────────────────────

export function renderMemoryInfo(statsText: string): ScreenContent {
  const text = `🧠 <b>Memory</b>\n\n${esc(statsText)}\n\n<i>Use /memory for full management.</i>`;
  const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
  return { text, keyboard };
}

// ─── Skills screen ────────────────────────────────────────────────

export interface SkillInfo {
  name: string;
  enabled: boolean;
}

export function renderSkillsList(skills: SkillInfo[]): ScreenContent {
  if (skills.length === 0) {
    const text = `🔧 <b>Skills</b>\n\nNo skills installed.\n\nUse <code>/skills install</code> to browse ClawHub.`;
    const keyboard = new InlineKeyboard().text("← Back", "nav:bk");
    return { text, keyboard };
  }

  const lines = skills.map(s =>
    `${s.enabled ? "✅" : "⬜"} ${esc(s.name)}`
  );
  const text = `🔧 <b>Skills</b> (${skills.filter(s => s.enabled).length}/${skills.length} enabled)\n\n${lines.join("\n")}`;

  const keyboard = new InlineKeyboard();
  for (const s of skills) {
    const key = truncKey(s.name);
    if (s.enabled) {
      keyboard.text(`⬜ Disable ${s.name}`, `nav:sk:dis:${key}`).row();
    } else {
      keyboard.text(`✅ Enable ${s.name}`, `nav:sk:en:${key}`).row();
    }
  }
  keyboard.text("← Back", "nav:bk");

  return { text, keyboard };
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | grep -E "pass|fail|PASS|FAIL|✓|×"
```

Expected: All screen renderer tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/adapters/telegram-menu/screens.ts
git commit -m "feat(telegram-menu): pure screen renderers for root/status/mcp/owl/memory/skills"
```

---

## Task 3: Root Menu Controller

**Files:**
- Create: `src/gateway/adapters/telegram-menu/root-menu.ts`
- Create: `src/gateway/adapters/telegram-menu/index.ts`

The controller fetches live data from the gateway and renders the appropriate screen, editing the nav message in place.

- [ ] **Step 1: Write failing tests** (add to existing test file)

Append to `__tests__/gateway/adapters/telegram-menu.test.ts`:

```typescript
import { TelegramRootMenu } from "../../../src/gateway/adapters/telegram-menu/root-menu.js";
import { vi } from "vitest";

describe("TelegramRootMenu.handleCallback", () => {
  function makeGateway(overrides: Record<string, unknown> = {}) {
    return {
      getConfig: () => ({ defaultModel: "claude-sonnet-4-6", providers: {} }),
      getOwl: () => ({ persona: { name: "Aria", emoji: "🦉" } }),
      getMcpManager: () => ({ listServers: () => [] }),
      getSkillsLoader: () => ({ getRegistry: () => ({ listEnabled: () => [] }) }),
      getMemoryRepo: () => null,
      getSessionStore: () => null,
      ...overrides,
    } as any;
  }

  function makeCtx(callbackData: string, userId = 1, chatId = 100, msgId = 42) {
    const editMessageText = vi.fn().mockResolvedValue({});
    const answerCallbackQuery = vi.fn().mockResolvedValue({});
    const reply = vi.fn().mockResolvedValue({ message_id: 99 });
    return {
      from: { id: userId },
      chat: { id: chatId },
      callbackQuery: { data: callbackData, message: { chat: { id: chatId }, message_id: msgId } },
      editMessageText,
      answerCallbackQuery,
      reply,
      api: { editMessageText, sendMessage: vi.fn().mockResolvedValue({ message_id: 99 }) },
    } as any;
  }

  it("nav:st renders status screen", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:st");
    // Open a nav session first so there's a messageId to edit
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:st");
    expect(ctx.editMessageText).toHaveBeenCalledWith(
      expect.stringContaining("claude-sonnet-4-6"),
      expect.objectContaining({ parse_mode: "HTML" }),
    );
  });

  it("nav:bk at root stays at root (no crash)", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:bk");
    menu["navState"].open(1, 100, 42);
    // Should not throw
    await menu.handleCallback(ctx, "nav:bk");
    expect(ctx.editMessageText).toHaveBeenCalled();
  });

  it("nav:st then nav:bk returns to root", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:st");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:st");
    // Now go back
    const ctx2 = makeCtx("nav:bk");
    menu["navState"].push(1, "status"); // simulate being on status
    await menu.handleCallback(ctx2, "nav:bk");
    expect(ctx2.editMessageText).toHaveBeenCalledWith(
      expect.stringContaining("Control Panel"),
      expect.any(Object),
    );
  });
});

describe("TelegramRootMenu.handleTextInput — keyboard button interception", () => {
  it("returns true and opens menu for '🎛 Menu'", async () => {
    const menu = new TelegramRootMenu({
      getConfig: () => ({ defaultModel: "m", providers: {} }),
      getOwl: () => ({ persona: { name: "X", emoji: "🦉" } }),
      getMcpManager: () => null,
      getSkillsLoader: () => null,
      getMemoryRepo: () => null,
      getSessionStore: () => null,
    } as any, {} as any, {} as any);
    const reply = vi.fn().mockResolvedValue({ message_id: 7 });
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply } as any;
    const consumed = await menu.handleTextInput(ctx, "🎛 Menu");
    expect(consumed).toBe(true);
    expect(reply).toHaveBeenCalled();
  });

  it("returns false for regular text", async () => {
    const menu = new TelegramRootMenu({
      getConfig: () => ({ defaultModel: "m", providers: {} }),
      getOwl: () => ({ persona: { name: "X", emoji: "🦉" } }),
      getMcpManager: () => null,
      getSkillsLoader: () => null,
      getMemoryRepo: () => null,
      getSessionStore: () => null,
    } as any, {} as any, {} as any);
    const ctx = { from: { id: 1 }, chat: { id: 100 } } as any;
    const consumed = await menu.handleTextInput(ctx, "hello world");
    expect(consumed).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | grep "Cannot find"
```

Expected: `Cannot find module '.../root-menu.js'`

- [ ] **Step 3: Implement root-menu.ts**

```typescript
/**
 * StackOwl — Telegram Unified Nav: Root Menu Controller
 *
 * Owns all nav:* callback dispatch and the persistent keyboard
 * button text interception ("🎛 Menu", "📊 Status", etc.).
 *
 * Delegates to existing menus for cfg/vc flows (they manage their own
 * message lifecycle and are not edited in-place by this controller).
 */

import type { Context } from "grammy";
import type { OwlGateway } from "../../core.js";
import type { TelegramConfigMenu } from "../telegram-config/menu.js";
import type { TelegramVoiceMenu } from "../telegram-config/voice-menu.js";
import { NavStateManager } from "./nav-state.js";
import { log } from "../../../logger.js";
import {
  renderRoot,
  renderStatus,
  renderMcpList,
  renderOwlList,
  renderMemoryInfo,
  renderSkillsList,
  type ScreenContent,
} from "./screens.js";
import { McpCommandRouter } from "../../commands/mcp-router.js";
import { dispatchMemoryCommand } from "../../commands/memory-router.js";
import { saveConfig } from "../../../config/loader.js";

// ─── Keyboard button texts (must match persistent keyboard in telegram.ts) ────

const KEYBOARD_BUTTON_MAP: Record<string, string> = {
  "🎛 Menu":    "root",
  "📊 Status":  "status",
  "🦉 Owls":   "owl",
  "⚙️ Settings": "cfg",
};

// ─── Controller ───────────────────────────────────────────────────

export class TelegramRootMenu {
  private navState = new NavStateManager();

  constructor(
    private gateway: OwlGateway,
    private configMenu: TelegramConfigMenu,
    private voiceMenu: TelegramVoiceMenu,
  ) {}

  // ─── Entry points ─────────────────────────────────────────────

  /** Handle /menu command — send a new nav message at root */
  async handleCommand(ctx: Context): Promise<void> {
    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return;

    log.telegram.debug("nav.handleCommand: entry", { userId });
    const content = renderRoot();
    try {
      const sent = await ctx.reply(content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      this.navState.open(userId, chatId, sent.message_id);
      log.telegram.debug("nav.handleCommand: exit", { messageId: sent.message_id });
    } catch (err) {
      log.telegram.error("nav.handleCommand: failed", err as Error);
    }
  }

  /**
   * Handle a nav:* callback_query.
   * Returns true if consumed.
   */
  async handleCallback(ctx: Context, data: string): Promise<boolean> {
    const userId = ctx.from?.id;
    if (!userId) return false;

    log.telegram.debug("nav.handleCallback: entry", { userId, data });

    // Ensure nav session exists (tap on stale nav message after restart)
    const chatId = ctx.callbackQuery?.message?.chat.id ?? ctx.chat?.id;
    const msgId = ctx.callbackQuery?.message?.message_id;
    if (!this.navState.get(userId) && chatId && msgId) {
      this.navState.open(userId, chatId, msgId);
    }

    try { await ctx.answerCallbackQuery(); } catch { /* expired — harmless */ }

    // ── Delegation: AI Config ──────────────────────────────────
    if (data === "nav:cfg") {
      log.telegram.debug("nav: delegating to configMenu");
      await this.configMenu.handleCommand(ctx);
      return true;
    }

    // ── Delegation: Voice ──────────────────────────────────────
    if (data === "nav:vc") {
      log.telegram.debug("nav: delegating to voiceMenu");
      await this.voiceMenu.handleCommand(ctx);
      return true;
    }

    // ── Back ───────────────────────────────────────────────────
    if (data === "nav:bk") {
      this.navState.pop(userId);
      const screen = this.navState.current(userId) ?? "root";
      await this.renderScreen(ctx, userId, screen);
      return true;
    }

    // ── Root ───────────────────────────────────────────────────
    if (data === "nav:r" || data === "nav:root") {
      while ((this.navState.current(userId) ?? "root") !== "root") {
        this.navState.pop(userId);
      }
      await this.renderScreen(ctx, userId, "root");
      return true;
    }

    // ── Status ─────────────────────────────────────────────────
    if (data === "nav:st") {
      this.navState.push(userId, "status");
      await this.renderScreen(ctx, userId, "status");
      return true;
    }

    // ── MCP list ───────────────────────────────────────────────
    if (data === "nav:mcp") {
      this.navState.push(userId, "mcp");
      await this.renderScreen(ctx, userId, "mcp");
      return true;
    }

    // ── MCP enable/disable/reconnect ───────────────────────────
    if (data.startsWith("nav:mcp:")) {
      await this.handleMcpAction(ctx, userId, data);
      return true;
    }

    // ── Owls list ──────────────────────────────────────────────
    if (data === "nav:owl") {
      this.navState.push(userId, "owl");
      await this.renderScreen(ctx, userId, "owl");
      return true;
    }

    // ── Owl switch ─────────────────────────────────────────────
    if (data.startsWith("nav:owl:sw:")) {
      await this.handleOwlSwitch(ctx, userId, data.slice("nav:owl:sw:".length));
      return true;
    }

    // ── Memory ─────────────────────────────────────────────────
    if (data === "nav:mem") {
      this.navState.push(userId, "memory");
      await this.renderScreen(ctx, userId, "memory");
      return true;
    }

    // ── Skills list ────────────────────────────────────────────
    if (data === "nav:sk") {
      this.navState.push(userId, "skills");
      await this.renderScreen(ctx, userId, "skills");
      return true;
    }

    // ── Skill enable/disable ───────────────────────────────────
    if (data.startsWith("nav:sk:")) {
      await this.handleSkillToggle(ctx, userId, data);
      return true;
    }

    return false;
  }

  /**
   * Intercept persistent keyboard button texts before they reach the gateway.
   * Returns true if the text was consumed.
   */
  async handleTextInput(ctx: Context, text: string): Promise<boolean> {
    const target = KEYBOARD_BUTTON_MAP[text];
    if (!target) return false;

    const userId = ctx.from?.id;
    const chatId = ctx.chat?.id;
    if (!userId || !chatId) return false;

    log.telegram.debug("nav.handleTextInput: keyboard button", { text, target });

    if (target === "cfg") {
      await this.configMenu.handleCommand(ctx);
      return true;
    }

    // For everything else: open/reopen the nav menu and navigate to target
    const content = renderRoot();
    try {
      const sent = await ctx.reply(content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      this.navState.open(userId, chatId, sent.message_id);

      if (target !== "root") {
        // Navigate immediately to the target screen
        await this.handleCallback(
          { ...ctx, callbackQuery: { data: `nav:${target}`, message: { chat: { id: chatId }, message_id: sent.message_id } } } as any,
          `nav:${target === "status" ? "st" : target}`,
        );
      }
    } catch (err) {
      log.telegram.error("nav.handleTextInput: failed", err as Error);
    }
    return true;
  }

  // ─── Screen renderer ──────────────────────────────────────────

  private async renderScreen(ctx: Context, userId: number, screen: string): Promise<void> {
    const content = await this.buildScreen(screen);
    const state = this.navState.get(userId);
    if (!state) return;

    try {
      const chatId = state.chatId;
      const msgId = state.messageId;
      await ctx.api.editMessageText(chatId, msgId, content.text, {
        parse_mode: "HTML",
        reply_markup: content.keyboard,
      });
      log.telegram.debug("nav.renderScreen: exit", { screen, chatId, msgId });
    } catch (err) {
      // Message unchanged — Telegram throws if content is identical (harmless)
      const msg = (err as Error).message ?? "";
      if (!msg.includes("message is not modified")) {
        log.telegram.warn("nav.renderScreen: edit failed", err as Error);
      }
    }
  }

  private async buildScreen(screen: string): Promise<ScreenContent> {
    switch (screen) {
      case "root":
        return renderRoot();

      case "status": {
        const config = this.gateway.getConfig();
        const owl = this.gateway.getOwl();
        const sessionStore = (this.gateway as any).getSessionStore?.();
        const sessionCount = sessionStore ? (await sessionStore.listAll?.() ?? []).length : 0;
        return renderStatus(config.defaultModel, owl.persona.emoji, owl.persona.name, sessionCount);
      }

      case "mcp": {
        const mgr = this.gateway.getMcpManager();
        const servers = mgr ? mgr.listServers().map((s: any) => ({
          name: s.name,
          connected: s.connected,
          toolCount: s.toolCount ?? 0,
        })) : [];
        return renderMcpList(servers);
      }

      case "owl": {
        const registry = (this.gateway as any).getSpecializedRegistry?.();
        if (registry) {
          await registry.loadAll((this.gateway as any).getWorkspacePath?.() ?? process.cwd()).catch(() => {});
        }
        const owls: { name: string; emoji: string; isPinned: boolean }[] =
          registry?.list?.() ?? [];
        const currentOwl = this.gateway.getOwl().persona.name;
        return renderOwlList(owls, currentOwl);
      }

      case "memory": {
        const repo = (this.gateway as any).getMemoryRepo?.();
        let statsText = "Memory repository unavailable.";
        if (repo) {
          try {
            statsText = await dispatchMemoryCommand("stats", [], { repo });
          } catch { /* keep default */ }
        }
        return renderMemoryInfo(statsText);
      }

      case "skills": {
        const loader = (this.gateway as any).getSkillsLoader?.();
        const registry = loader?.getRegistry?.();
        const skills = registry ? registry.listEnabled().map((s: any) => ({
          name: s.name,
          enabled: s.enabled ?? true,
        })) : [];
        return renderSkillsList(skills);
      }

      default:
        return renderRoot();
    }
  }

  // ─── Action handlers ──────────────────────────────────────────

  private async handleMcpAction(ctx: Context, userId: number, data: string): Promise<void> {
    // data format: nav:mcp:{verb}:{serverName}
    const parts = data.split(":");  // ["nav", "mcp", verb, ...serverName]
    const verb = parts[2];
    const serverName = parts.slice(3).join(":");

    const mcpManager = this.gateway.getMcpManager();
    const toolRegistry = this.gateway.getToolRegistry();
    if (!mcpManager || !toolRegistry) {
      await ctx.answerCallbackQuery({ text: "MCP not available" });
      return;
    }

    const mcpVerb = verb === "en" ? "enable" : verb === "dis" ? "disable" : "reconnect";
    try {
      await McpCommandRouter.dispatch(mcpVerb, [serverName], {
        mcpManager,
        toolRegistry,
        config: this.gateway.getConfig(),
        basePath: (this.gateway as any).getWorkspacePath?.() ?? process.cwd(),
        saveConfig,
      });
      await ctx.answerCallbackQuery({ text: `✅ ${mcpVerb} ${serverName}` });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` });
    }

    // Refresh MCP list
    await this.renderScreen(ctx, userId, "mcp");
  }

  private async handleOwlSwitch(ctx: Context, userId: number, owlName: string): Promise<void> {
    try {
      const { dispatchOwlCommand } = await import("../../commands/owl-command.js");
      const registry = (this.gateway as any).getSpecializedRegistry?.();
      await dispatchOwlCommand("pin", [owlName], {
        registry,
        userId: String(userId),
        workspacePath: (this.gateway as any).getWorkspacePath?.() ?? process.cwd(),
        gateway: this.gateway as any,
      });
      await ctx.answerCallbackQuery({ text: `🦉 Switched to ${owlName}` });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` });
    }

    // Refresh owl list
    await this.renderScreen(ctx, userId, "owl");
  }

  private async handleSkillToggle(ctx: Context, userId: number, data: string): Promise<void> {
    // data: nav:sk:en:{name} or nav:sk:dis:{name}
    const parts = data.split(":");
    const action = parts[2]; // "en" or "dis"
    const skillName = parts.slice(3).join(":");
    const enable = action === "en";

    const loader = (this.gateway as any).getSkillsLoader?.();
    const registry = loader?.getRegistry?.();
    if (!registry) {
      await ctx.answerCallbackQuery({ text: "Skills registry unavailable" });
      return;
    }

    try {
      if (enable) {
        registry.enable?.(skillName);
      } else {
        registry.disable?.(skillName);
      }
      await ctx.answerCallbackQuery({ text: `${enable ? "✅ Enabled" : "⬜ Disabled"}: ${skillName}` });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      await ctx.answerCallbackQuery({ text: `❌ ${msg.slice(0, 100)}` });
    }

    // Refresh skills list
    await this.renderScreen(ctx, userId, "skills");
  }
}
```

- [ ] **Step 4: Create index.ts**

```typescript
export { TelegramRootMenu } from "./root-menu.js";
export { NavStateManager } from "./nav-state.js";
export type { NavState } from "./nav-state.js";
export type { ScreenContent, McpServerInfo, OwlInfo, SkillInfo } from "./screens.js";
```

- [ ] **Step 5: Run all tests**

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts 2>&1 | tail -20
```

Expected: All tests PASS (NavStateManager + screens + TelegramRootMenu).

- [ ] **Step 6: Commit**

```bash
git add src/gateway/adapters/telegram-menu/
git commit -m "feat(telegram-menu): root menu controller with nav routing and keyboard intercept"
```

---

## Task 4: Wire Into TelegramAdapter

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`

Four changes in telegram.ts:
1. Import `TelegramRootMenu` and instantiate it
2. Add `setMyCommands` + `setChatMenuButton` in `start()`  
3. Update `/start` handler to send persistent Reply Keyboard
4. Wire `/menu` command + `nav:*` callback + keyboard text interception

- [ ] **Step 1: Write failing integration test** (add to existing test file)

Append to `__tests__/gateway/adapters/telegram-menu.test.ts`:

```typescript
// Integration: verify the KEYBOARD_BUTTON_MAP texts match what's in the persistent keyboard
// This is a contract test — if the button labels change in telegram.ts the interception breaks.
describe("Persistent keyboard contract", () => {
  it("keyboard button labels match KEYBOARD_BUTTON_MAP in root-menu.ts", async () => {
    // The persistent keyboard sends these exact strings as text messages.
    // TelegramRootMenu.handleTextInput must recognise all of them.
    const { TelegramRootMenu } = await import(
      "../../../src/gateway/adapters/telegram-menu/root-menu.js"
    );
    const fakeGateway = {
      getConfig: () => ({ defaultModel: "m", providers: {} }),
      getOwl: () => ({ persona: { name: "X", emoji: "🦉" } }),
      getMcpManager: () => null,
      getSkillsLoader: () => null,
      getMemoryRepo: () => null,
      getSessionStore: () => null,
    } as any;
    const menu = new TelegramRootMenu(fakeGateway, {} as any, {} as any);

    const reply = vi.fn().mockResolvedValue({ message_id: 1 });
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply } as any;

    // All persistent keyboard button labels must be consumed
    for (const label of ["🎛 Menu", "📊 Status", "🦉 Owls", "⚙️ Settings"]) {
      const result = await menu.handleTextInput(ctx, label);
      expect(result, `"${label}" should be consumed`).toBe(true);
    }
  });
});
```

- [ ] **Step 2: Run to confirm test passes already** (it tests exported constants, not telegram.ts wiring)

```bash
npx vitest run __tests__/gateway/adapters/telegram-menu.test.ts --reporter=verbose 2>&1 | tail -10
```

Expected: All tests PASS (the contract test uses the exported map, no telegram.ts changes needed for the test to pass).

- [ ] **Step 3: Add import and private field to TelegramAdapter**

In `src/gateway/adapters/telegram.ts`, find the imports block (around line 14–34) and add:

```typescript
import { TelegramRootMenu } from "./telegram-menu/index.js";
```

Find the private field declarations (around line 58–75) and add after `private voiceMenu: TelegramVoiceMenu;`:

```typescript
/** Unified nav control panel controller */
private rootMenu: TelegramRootMenu;
```

- [ ] **Step 4: Instantiate TelegramRootMenu in constructor**

In the constructor, find the block after `this.voiceMenu` is instantiated (around line 110–135). Add immediately after the voiceMenu block:

```typescript
    // ── Unified nav menu ─────────────────────────────────────────
    this.rootMenu = new TelegramRootMenu(gateway, this.configMenu, this.voiceMenu);
```

- [ ] **Step 5: Add setMyCommands + setChatMenuButton in start()**

In `start()`, find line after `const me = await this.bot.api.getMe();` (around line 172). Add:

```typescript
    // Register bot commands for native Telegram command discovery (bottom-left menu button)
    await this.bot.api.setMyCommands([
      { command: "menu",   description: "Open control panel" },
      { command: "status", description: "Current owl & model" },
      { command: "config", description: "AI provider & model" },
      { command: "voice",  description: "Voice settings" },
      { command: "skills", description: "Manage skills" },
      { command: "mcp",    description: "MCP servers" },
      { command: "memory", description: "Memory management" },
      { command: "owl",    description: "Owl personas" },
      { command: "reset",  description: "Clear session" },
    ]).catch(err => log.telegram.warn(`setMyCommands failed: ${err instanceof Error ? err.message : err}`));

    await this.bot.api.setChatMenuButton({
      menu_button: { type: "commands" },
    }).catch(err => log.telegram.warn(`setChatMenuButton failed: ${err instanceof Error ? err.message : err}`));
```

- [ ] **Step 6: Add persistent Reply Keyboard to /start handler**

In `setupHandlers()`, find the `/start` handler (around line 253). Replace the existing `ctx.reply(...)` call:

```typescript
    this.bot.command("start", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);

      const { Keyboard } = await import("grammy");
      const persistentKeyboard = new Keyboard()
        .text("🎛 Menu").text("📊 Status")
        .row()
        .text("🦉 Owls").text("⚙️ Settings")
        .resized()
        .persistent();

      await ctx.reply(
        `${owl.persona.emoji} *${this.esc(owl.persona.name)}* reporting for duty\\!\n\n` +
          `I'm your personal AI assistant\\. Talk to me naturally — I'll handle the rest\\. 🦉\n\n` +
          `Use the buttons below or tap ☰ for all commands\\.`,
        {
          parse_mode: "MarkdownV2",
          reply_markup: persistentKeyboard,
        },
      );
    });
```

- [ ] **Step 7: Add /menu command in setupHandlers()**

After the `/voice` command handler (around line 287), add:

```typescript
    // ── /menu — Unified inline control panel ─────────────────────
    this.bot.command("menu", async (ctx) => {
      if (!this.isAllowed(ctx)) return;
      this.trackChat(ctx.chat.id);
      await this.rootMenu.handleCommand(ctx);
    });
```

- [ ] **Step 8: Add nav:* to the callback_query router**

In the `callback_query:data` handler (around line 836), add at the top of the switch logic, before the `wiz:` check:

```typescript
      // ── Unified nav menu callbacks ────────────────────────────
      if (data.startsWith("nav:")) {
        if (!this.isAllowed(ctx)) {
          await ctx.answerCallbackQuery({ text: "⛔ Not authorised." });
          return;
        }
        await this.rootMenu.handleCallback(ctx, data);
        return;
      }
```

- [ ] **Step 9: Add keyboard button text interception in message:text handler**

In the `message:text` handler, find the `configConsumed` interception (around line 457). Add immediately after it:

```typescript
      // ─── Persistent keyboard button interception ──────────────
      // Must run before dedup so the nav message can be sent fresh.
      const navConsumed = await this.rootMenu.handleTextInput(ctx, text);
      if (navConsumed) return;
      // ─────────────────────────────────────────────────────────
```

- [ ] **Step 10: Run full test suite**

```bash
npx vitest run 2>&1 | tail -10
```

Expected: Same pass count as before (≥ 3423 passing, 0 new failures).

- [ ] **Step 11: TypeScript compile check**

```bash
npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors.

- [ ] **Step 12: Commit**

```bash
git add src/gateway/adapters/telegram.ts src/gateway/adapters/telegram-menu/
git commit -m "feat(telegram): /menu command, persistent keyboard, bot command registry, nav:* routing"
```

---

## Task 5: Smoke Test End-to-End

This task has no code changes — it verifies the feature works in a real Telegram bot session before calling it done.

- [ ] **Step 1: Start the bot in dev**

```bash
npm start -- telegram 2>&1 | grep -E "Connected|Running|error" &
```

- [ ] **Step 2: Open your Telegram bot and verify**

Checklist:
- [ ] Tap the ☰ (commands) button bottom-left — all 9 commands listed with descriptions
- [ ] Send `/start` — persistent keyboard appears with 4 buttons: 🎛 Menu, 📊 Status, 🦉 Owls, ⚙️ Settings
- [ ] Tap **🎛 Menu** — inline control panel appears with 7 buttons
- [ ] Tap **🤖 AI Config** — TelegramConfigMenu opens in a new message (existing flow)
- [ ] Back in the nav panel, tap **📊 Status** — message edits in place to show model + owl
- [ ] Tap **← Back** — message edits back to root panel
- [ ] Tap **🔌 MCP Servers** — shows MCP list (or empty state message)
- [ ] Tap **🦉 Owls** — shows owl list; if >1 owl, Switch button appears for non-active ones
- [ ] Tap **🔧 Skills** — shows skills list with enable/disable buttons
- [ ] Tap **📊 Status** (from keyboard) — nav panel opens then navigates to status
- [ ] Send `/menu` directly — opens control panel

- [ ] **Step 3: Kill the bot**

```bash
kill %1
```

- [ ] **Step 4: Final commit if any tweaks made during smoke test**

```bash
git add -p
git commit -m "fix(telegram-menu): smoke test tweaks"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| `setMyCommands` + `setChatMenuButton` | Task 4, Steps 5 |
| Persistent Reply Keyboard on `/start` | Task 4, Step 6 |
| `/menu` command | Task 4, Step 7 |
| `nav:*` callback routing | Task 4, Step 8 |
| Keyboard button text interception | Task 4, Step 9 |
| Root control panel screen | Task 2, `renderRoot` |
| Status sub-screen | Task 2, `renderStatus`; Task 3 routing |
| MCP sub-screen with enable/disable | Task 2, `renderMcpList`; Task 3 `handleMcpAction` |
| Owls sub-screen with switch | Task 2, `renderOwlList`; Task 3 `handleOwlSwitch` |
| Memory sub-screen | Task 2, `renderMemoryInfo`; Task 3 routing |
| Skills sub-screen with enable/disable | Task 2, `renderSkillsList`; Task 3 `handleSkillToggle` |
| Back navigation | Task 1 `NavStateManager.pop`; Task 3 `nav:bk` handler |
| AI Config delegate | Task 3 `nav:cfg` → `configMenu.handleCommand` |
| Voice delegate | Task 3 `nav:vc` → `voiceMenu.handleCommand` |

All requirements covered.

### Placeholder scan

No TBD, TODO, "add appropriate handling", or "similar to Task N" patterns. All code blocks are complete.

### Type consistency

- `ScreenContent` defined in `screens.ts`, imported via `index.ts` in `root-menu.ts` ✅
- `NavState` defined in `nav-state.ts`, used only internally in `NavStateManager` ✅
- `McpServerInfo`, `OwlInfo`, `SkillInfo` defined in `screens.ts`, used in `buildScreen()` ✅
- `TelegramRootMenu` constructor signature: `(gateway: OwlGateway, configMenu: TelegramConfigMenu, voiceMenu: TelegramVoiceMenu)` — matches instantiation in telegram.ts ✅

---

Plan complete and saved to `docs/superpowers/plans/2026-05-14-telegram-unified-nav.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
