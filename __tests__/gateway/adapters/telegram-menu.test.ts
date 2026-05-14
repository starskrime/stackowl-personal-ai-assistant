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
