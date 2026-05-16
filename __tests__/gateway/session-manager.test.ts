import { describe, it, expect, vi, beforeEach } from "vitest";
import { SessionManager } from "../../src/gateway/session-manager.js";
import type { SessionStore, Session } from "../../src/memory/store.js";
import type { SessionService } from "../../src/session/service.js";

const makeSession = (id: string): Session => ({
  id,
  messages: [],
  metadata: {
    owlName: "test-owl",
    startedAt: Date.now(),
    lastUpdatedAt: Date.now(),
  },
});

const makeStore = (existing?: Session): SessionStore =>
  ({
    loadSession: vi.fn().mockResolvedValue(existing ?? null),
    createSession: vi.fn().mockReturnValue(makeSession("new-session")),
    saveSession: vi.fn().mockResolvedValue(undefined),
  } as unknown as SessionStore);

describe("SessionManager", () => {
  let store: SessionStore;

  beforeEach(() => {
    store = makeStore();
  });

  it("creates a new session when none exists in store", async () => {
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s1", userId: "u1", channelId: "cli", text: "", id: "m1" };
    const session = await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledWith("s1");
    expect(store.createSession).toHaveBeenCalled();
    expect(session.id).toBe("s1");
  });

  it("returns cached session on second call (no store read)", async () => {
    const existing = makeSession("s1");
    store = makeStore(existing);
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s1", userId: "u1", channelId: "cli", text: "", id: "m1" };
    await mgr.getOrCreate(msg);
    await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledTimes(1);
  });

  it("delegates to SessionService when provided", async () => {
    const sessionSvc = {
      getOrCreate: vi.fn().mockResolvedValue(makeSession("s2")),
    } as unknown as SessionService;
    const mgr = new SessionManager({ sessionStore: store, sessionService: sessionSvc } as any);
    const msg = { sessionId: "s2", userId: "u2", channelId: "telegram", text: "", id: "m2" };
    const result = await mgr.getOrCreate(msg);
    expect(sessionSvc.getOrCreate).toHaveBeenCalledWith("s2", "u2", expect.any(String));
    expect(result.id).toBe("s2");
  });

  it("saves session to store", async () => {
    const session = makeSession("s3");
    const mgr = new SessionManager({ sessionStore: store } as any);
    await mgr.save(session);
    expect(store.saveSession).toHaveBeenCalledWith(session);
  });

  it("invalidate removes session from cache", async () => {
    const existing = makeSession("s4");
    store = makeStore(existing);
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg = { sessionId: "s4", userId: "u4", channelId: "cli", text: "", id: "m4" };
    await mgr.getOrCreate(msg);
    mgr.invalidate("s4");
    await mgr.getOrCreate(msg);
    expect(store.loadSession).toHaveBeenCalledTimes(2); // cache was cleared
  });

  it("getActiveCount returns number of cached sessions", async () => {
    const mgr = new SessionManager({ sessionStore: store } as any);
    const msg1 = { sessionId: "s5", userId: "u5", channelId: "cli", text: "", id: "m5" };
    const msg2 = { sessionId: "s6", userId: "u6", channelId: "cli", text: "", id: "m6" };
    await mgr.getOrCreate(msg1);
    await mgr.getOrCreate(msg2);
    expect(mgr.getActiveCount()).toBe(2);
  });
});
