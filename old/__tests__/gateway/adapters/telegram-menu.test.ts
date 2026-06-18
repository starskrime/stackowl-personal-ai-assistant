import { describe, it, expect, beforeEach } from "vitest";
import { InlineKeyboard } from "grammy";
import { NavStateManager } from "../../../src/gateway/adapters/telegram-menu/nav-state.js";
import {
  renderRoot,
  renderStatus,
  renderMcpList,
  renderOwlList,
  renderMemoryInfo,
  renderSkillsList,
} from "../../../src/gateway/adapters/telegram-menu/screens.js";

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

describe("screens.ts — renderRoot", () => {
  it("returns HTML text and buttons for all sections", () => {
    const sc = renderRoot();
    expect(sc.text).toContain("Control Panel");
    const allButtons = sc.keyboard.inline_keyboard.flat().map((b: any) => b.callback_data);
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
    const allButtons = sc.keyboard.inline_keyboard.flat().map((b: any) => b.callback_data);
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
    const cbData = sc.keyboard.inline_keyboard.flat().map((b: any) => b.callback_data);
    expect(cbData.some((d: string) => d.includes("nav:mcp:en:") || d.includes("nav:mcp:dis:"))).toBe(true);
  });

  it("shows empty state message when no servers", () => {
    const sc = renderMcpList([]);
    expect(sc.text).toContain("No MCP");
  });
});

describe("screens.ts — renderOwlList", () => {
  it("marks current owl and provides switch buttons for others", () => {
    const owls = [
      { name: "Aria", emoji: "🦉", isPinned: true },
      { name: "Max", emoji: "🤖", isPinned: false },
    ];
    const sc = renderOwlList(owls, "Aria");
    expect(sc.text).toContain("Aria");
    const cbData = sc.keyboard.inline_keyboard.flat().map((b: any) => b.callback_data);
    expect(cbData).toContain("nav:owl:sw:Max");
    expect(cbData).not.toContain("nav:owl:sw:Aria");
  });
});

describe("screens.ts — renderSkillsList", () => {
  it("shows enable/disable buttons per skill", () => {
    const skills = [
      { name: "remember", enabled: true },
      { name: "web-search", enabled: false },
    ];
    const sc = renderSkillsList(skills);
    const cbData = sc.keyboard.inline_keyboard.flat().map((b: any) => b.callback_data);
    expect(cbData).toContain("nav:sk:dis:remember");
    expect(cbData).toContain("nav:sk:en:web-search");
  });
});

import { TelegramRootMenu } from "../../../src/gateway/adapters/telegram-menu/root-menu.js";
import { vi } from "vitest";

describe("TelegramRootMenu.handleCallback", () => {
  function makeGateway(overrides: Record<string, unknown> = {}) {
    return {
      getConfig: () => ({ defaultModel: "claude-sonnet-4-6", providers: {} }),
      getOwl: () => ({ persona: { name: "Aria", emoji: "🦉" } }),
      getMcpManager: () => ({ listServers: () => [] }),
      getToolRegistry: () => null,
      getSkillsLoader: () => ({ getRegistry: () => ({ listEnabled: () => [] }) }),
      getMemoryRepo: () => null,
      getSessionStore: () => null,
      getWorkspacePath: () => "/tmp",
      getSpecializedRegistry: () => null,
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
      api: {
        editMessageText,
        sendMessage: vi.fn().mockResolvedValue({ message_id: 99 }),
      },
    } as any;
  }

  it("nav:st renders status screen (edits message in place)", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:st");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:st");
    expect(ctx.api.editMessageText).toHaveBeenCalledWith(
      100, 42,
      expect.stringContaining("claude-sonnet-4-6"),
      expect.objectContaining({ parse_mode: "HTML" }),
    );
  });

  it("nav:bk at root stays at root screen", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:bk");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:bk");
    // Should edit to root panel (contains "Control Panel")
    expect(ctx.api.editMessageText).toHaveBeenCalledWith(
      100, 42,
      expect.stringContaining("Control Panel"),
      expect.any(Object),
    );
  });

  it("nav:st then nav:bk returns to root", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx1 = makeCtx("nav:st");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx1, "nav:st");

    const ctx2 = makeCtx("nav:bk");
    await menu.handleCallback(ctx2, "nav:bk");
    expect(ctx2.api.editMessageText).toHaveBeenCalledWith(
      100, 42,
      expect.stringContaining("Control Panel"),
      expect.any(Object),
    );
  });

  it("nav:cfg delegates to configMenu.handleCommand", async () => {
    const configMenu = { handleCommand: vi.fn().mockResolvedValue(undefined) };
    const menu = new TelegramRootMenu(makeGateway(), configMenu as any, {} as any);
    const ctx = makeCtx("nav:cfg");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:cfg");
    expect(configMenu.handleCommand).toHaveBeenCalledWith(ctx);
  });

  it("nav:vc delegates to voiceMenu.handleCommand", async () => {
    const voiceMenu = { handleCommand: vi.fn().mockResolvedValue(undefined) };
    const menu = new TelegramRootMenu(makeGateway(), {} as any, voiceMenu as any);
    const ctx = makeCtx("nav:vc");
    menu["navState"].open(1, 100, 42);
    await menu.handleCallback(ctx, "nav:vc");
    expect(voiceMenu.handleCommand).toHaveBeenCalledWith(ctx);
  });

  it("returns false for unknown nav data", async () => {
    const menu = new TelegramRootMenu(makeGateway(), {} as any, {} as any);
    const ctx = makeCtx("nav:unknown:action");
    menu["navState"].open(1, 100, 42);
    const result = await menu.handleCallback(ctx, "nav:unknown:action");
    expect(result).toBe(false);
  });
});

describe("TelegramRootMenu.handleTextInput", () => {
  function makeMenuWithFakes() {
    const configMenu = { handleCommand: vi.fn().mockResolvedValue(undefined) };
    const gateway = {
      getConfig: () => ({ defaultModel: "m", providers: {} }),
      getOwl: () => ({ persona: { name: "X", emoji: "🦉" } }),
      getMcpManager: () => null,
      getToolRegistry: () => null,
      getSkillsLoader: () => null,
      getMemoryRepo: () => null,
      getSessionStore: () => null,
      getWorkspacePath: () => "/tmp",
      getSpecializedRegistry: () => null,
    } as any;
    const reply = vi.fn().mockResolvedValue({ message_id: 7 });
    return { menu: new TelegramRootMenu(gateway, configMenu as any, {} as any), configMenu, reply };
  }

  it("returns true and sends nav panel for '🎛 Menu'", async () => {
    const { menu, reply } = makeMenuWithFakes();
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply, api: { editMessageText: vi.fn() } } as any;
    const consumed = await menu.handleTextInput(ctx, "🎛 Menu");
    expect(consumed).toBe(true);
    expect(reply).toHaveBeenCalled();
  });

  it("returns true for '📊 Status'", async () => {
    const { menu, reply } = makeMenuWithFakes();
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply, api: { editMessageText: vi.fn() } } as any;
    const consumed = await menu.handleTextInput(ctx, "📊 Status");
    expect(consumed).toBe(true);
  });

  it("returns false for regular chat text", async () => {
    const { menu } = makeMenuWithFakes();
    const ctx = { from: { id: 1 }, chat: { id: 100 } } as any;
    const consumed = await menu.handleTextInput(ctx, "hello world");
    expect(consumed).toBe(false);
  });

  it("returns true and calls configMenu for '⚙️ Settings'", async () => {
    const { menu, configMenu, reply } = makeMenuWithFakes();
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply } as any;
    const consumed = await menu.handleTextInput(ctx, "⚙️ Settings");
    expect(consumed).toBe(true);
    expect(configMenu.handleCommand).toHaveBeenCalled();
  });
});

describe("Persistent keyboard contract — button labels match KEYBOARD_BUTTON_MAP", () => {
  it("all 4 keyboard button labels are consumed by handleTextInput", async () => {
    const { TelegramRootMenu } = await import(
      "../../../src/gateway/adapters/telegram-menu/root-menu.js"
    );
    const fakeGateway = {
      getConfig: () => ({ defaultModel: "m", providers: {} }),
      getOwl: () => ({ persona: { name: "X", emoji: "🦉" } }),
      getMcpManager: () => null,
      getToolRegistry: () => null,
      getSkillsLoader: () => null,
      getMemoryRepo: () => null,
      getSessionStore: () => null,
      getWorkspacePath: () => "/tmp",
      getSpecializedRegistry: () => null,
    } as any;
    const configMenu = { handleCommand: vi.fn().mockResolvedValue(undefined) };
    const menu = new TelegramRootMenu(fakeGateway, configMenu as any, {} as any);

    const reply = vi.fn().mockResolvedValue({ message_id: 1 });
    const api = { editMessageText: vi.fn().mockResolvedValue({}) };
    const ctx = { from: { id: 1 }, chat: { id: 100 }, reply, api } as any;

    const labels = ["🎛 Menu", "📊 Status", "🦉 Owls", "⚙️ Settings"];
    for (const label of labels) {
      // Reset mocks between calls
      reply.mockClear();
      const result = await menu.handleTextInput(ctx, label);
      expect(result, `"${label}" should be consumed`).toBe(true);
    }

    // Regular text must NOT be consumed
    const result = await menu.handleTextInput(ctx, "hello there");
    expect(result).toBe(false);
  });
});
