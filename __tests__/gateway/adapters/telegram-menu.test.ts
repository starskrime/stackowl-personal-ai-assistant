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
