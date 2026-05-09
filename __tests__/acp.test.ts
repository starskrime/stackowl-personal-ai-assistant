import { describe, it, expect, beforeEach, vi } from "vitest";
import { ACPBackpressure } from "../src/acp/backpressure.js";
import { SessionBridgeFactory } from "../src/acp/bridge.js";
import { ACPRouter } from "../src/acp/router.js";
import type {
  ACPMessage,
  ACPMessageHandler,
  ACPCapability,
} from "../src/acp/types.js";
import type { AgentRegistry } from "../src/agents/types.js";
import type { EventBus } from "../src/events/bus.js";
import type { SessionStore } from "../src/memory/store.js";

function makeMessage(overrides: Partial<ACPMessage> = {}): ACPMessage {
  return {
    id: "msg_" + Math.random().toString(36).slice(2),
    from: "test-agent",
    to: "target-agent",
    channel: "test-channel",
    payload: { data: "test" },
    timestamp: Date.now(),
    ...overrides,
  };
}

function makeMockEventBus(): EventBus {
  return {
    emit: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    once: vi.fn(),
  };
}

function makeMockAgentRegistry(): AgentRegistry {
  return {
    register: vi.fn(),
    unregister: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    findByCapability: vi.fn(),
  };
}

describe("ACPBackpressure", () => {
  let backpressure: ACPBackpressure;

  beforeEach(() => {
    backpressure = new ACPBackpressure(5);
  });

  describe("enqueue()", () => {
    it("returns delivered when inbox has space", () => {
      const msg = makeMessage();
      const status = backpressure.enqueue("agent1", msg);
      expect(status).toBe("delivered");
    });

    it("returns delivered and enqueues message", () => {
      const msg = makeMessage();
      backpressure.enqueue("agent1", msg);
      expect(backpressure.getInboxSize("agent1")).toBe(1);
    });

    it("returns expired when message TTL is exceeded", () => {
      const oldTimestamp = Date.now() - 200;
      const msg = makeMessage({ timestamp: oldTimestamp, ttlMs: 100 });
      const status = backpressure.enqueue("agent1", msg);
      expect(status).toBe("expired");
    });

    it("returns backpressure when inbox is full", () => {
      for (let i = 0; i < 5; i++) {
        backpressure.enqueue("agent1", makeMessage({ id: `msg_${i}` }));
      }
      const msg = makeMessage({ id: "overflow" });
      const status = backpressure.enqueue("agent1", msg);
      expect(status).toBe("backpressure");
    });

    it("creates separate inboxes for different agents", () => {
      backpressure.enqueue("agent1", makeMessage());
      backpressure.enqueue("agent2", makeMessage());
      expect(backpressure.getInboxSize("agent1")).toBe(1);
      expect(backpressure.getInboxSize("agent2")).toBe(1);
    });
  });

  describe("dequeue()", () => {
    it("returns null when inbox is empty", () => {
      const result = backpressure.dequeue("nonexistent");
      expect(result).toBeNull();
    });

    it("returns and removes the oldest message", () => {
      const msg1 = makeMessage({ id: "msg1" });
      const msg2 = makeMessage({ id: "msg2" });
      backpressure.enqueue("agent1", msg1);
      backpressure.enqueue("agent1", msg2);

      const dequeued = backpressure.dequeue("agent1");
      expect(dequeued?.id).toBe("msg1");
      expect(backpressure.getInboxSize("agent1")).toBe(1);
    });

    it("skips expired messages and returns next valid one", () => {
      const oldTimestamp = Date.now() - 200;
      const expiredMsg = makeMessage({
        id: "expired",
        timestamp: oldTimestamp,
        ttlMs: 100,
      });
      const validMsg = makeMessage({ id: "valid" });
      backpressure.enqueue("agent1", expiredMsg);
      backpressure.enqueue("agent1", validMsg);

      const dequeued = backpressure.dequeue("agent1");
      expect(dequeued?.id).toBe("valid");
    });
  });

  describe("onAvailable()", () => {
    it("registers callback for agent", () => {
      const callback = vi.fn();
      backpressure.onAvailable("agent1", callback);
      expect(callback).not.toHaveBeenCalled();
    });

    it("notifies callback when message is enqueued", () => {
      const callback = vi.fn();
      backpressure.onAvailable("agent1", callback);

      const msg = makeMessage();
      backpressure.enqueue("agent1", msg);

      expect(callback).toHaveBeenCalledTimes(1);
    });
  });

  describe("getInboxSize()", () => {
    it("returns 0 for unknown agent", () => {
      expect(backpressure.getInboxSize("unknown")).toBe(0);
    });

    it("returns correct size after enqueueing", () => {
      backpressure.enqueue("agent1", makeMessage());
      backpressure.enqueue("agent1", makeMessage());
      expect(backpressure.getInboxSize("agent1")).toBe(2);
    });
  });

  describe("clearInbox()", () => {
    it("removes all messages for an agent", () => {
      backpressure.enqueue("agent1", makeMessage());
      backpressure.enqueue("agent1", makeMessage());
      backpressure.clearInbox("agent1");
      expect(backpressure.getInboxSize("agent1")).toBe(0);
    });

    it("does not affect other agents", () => {
      backpressure.enqueue("agent1", makeMessage());
      backpressure.enqueue("agent2", makeMessage());
      backpressure.clearInbox("agent1");
      expect(backpressure.getInboxSize("agent2")).toBe(1);
    });
  });

  describe("pruneExpired()", () => {
    it("returns 0 when no messages are expired", () => {
      backpressure.enqueue("agent1", makeMessage());
      const pruned = backpressure.pruneExpired();
      expect(pruned).toBe(0);
    });

    it("removes expired messages and returns count", () => {
      vi.useFakeTimers();
      vi.setSystemTime(100);

      backpressure.enqueue(
        "agent1",
        makeMessage({ id: "expired1", timestamp: 0, ttlMs: 100 }),
      );
      backpressure.enqueue(
        "agent1",
        makeMessage({ id: "expired2", timestamp: 0, ttlMs: 100 }),
      );
      backpressure.enqueue("agent1", makeMessage({ id: "valid" }));

      vi.setSystemTime(300);

      const pruned = backpressure.pruneExpired();
      expect(pruned).toBe(2);
      expect(backpressure.getInboxSize("agent1")).toBe(1);

      vi.useRealTimers();
    });

    it("handles multiple agents", () => {
      vi.useFakeTimers();
      vi.setSystemTime(100);

      backpressure.enqueue(
        "agent1",
        makeMessage({ id: "a1-expired", timestamp: 0, ttlMs: 100 }),
      );
      backpressure.enqueue("agent2", makeMessage({ id: "a2-valid" }));

      vi.setSystemTime(300);

      const pruned = backpressure.pruneExpired();
      expect(pruned).toBe(1);
      expect(backpressure.getInboxSize("agent1")).toBe(0);
      expect(backpressure.getInboxSize("agent2")).toBe(1);

      vi.useRealTimers();
    });
  });
});

describe("SessionBridgeFactory", () => {
  let sessionStore: SessionStore;
  let factory: SessionBridgeFactory;

  beforeEach(() => {
    sessionStore = {
      loadSession: vi.fn(),
    } as unknown as SessionStore;
    factory = new SessionBridgeFactory(sessionStore);
  });

  describe("createBridge()", () => {
    it("creates a bridge with correct sessionId", async () => {
      const permissions = {
        readHistory: true,
        readPellets: false,
        writeContext: false,
        maxHistoryDepth: 10,
      };
      const bridge = factory.createBridge("session_123", permissions);
      expect(bridge.sessionId).toBe("session_123");
    });

    describe("getHistory()", () => {
      it("returns empty array when readHistory is false", async () => {
        const bridge = factory.createBridge("session_123", {
          readHistory: false,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 10,
        });

        const history = await bridge.getHistory();
        expect(history).toEqual([]);
        expect(sessionStore.loadSession).not.toHaveBeenCalled();
      });

      it("returns empty array when session not found", async () => {
        (sessionStore.loadSession as any).mockResolvedValue(null);

        const bridge = factory.createBridge("nonexistent", {
          readHistory: true,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 10,
        });

        const history = await bridge.getHistory();
        expect(history).toEqual([]);
      });

      it("returns messages limited by maxHistoryDepth", async () => {
        const messages = [
          { role: "user", content: "Hello" },
          { role: "assistant", content: "Hi" },
          { role: "user", content: "How are you?" },
        ];
        (sessionStore.loadSession as any).mockResolvedValue({
          id: "session_123",
          messages,
          metadata: {
            owlName: "Test",
            startedAt: Date.now(),
            lastUpdatedAt: Date.now(),
          },
        });

        const bridge = factory.createBridge("session_123", {
          readHistory: true,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 2,
        });

        const history = await bridge.getHistory();
        expect(history).toHaveLength(2);
      });

      it("respects limit parameter when lower than maxHistoryDepth", async () => {
        const messages = [
          { role: "user", content: "Hello" },
          { role: "assistant", content: "Hi" },
          { role: "user", content: "How are you?" },
        ];
        (sessionStore.loadSession as any).mockResolvedValue({
          id: "session_123",
          messages,
          metadata: {
            owlName: "Test",
            startedAt: Date.now(),
            lastUpdatedAt: Date.now(),
          },
        });

        const bridge = factory.createBridge("session_123", {
          readHistory: true,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 10,
        });

        const history = await bridge.getHistory(1);
        expect(history).toHaveLength(1);
      });
    });

    describe("getContext() / setContext()", () => {
      it("returns undefined for unknown context keys", () => {
        const bridge = factory.createBridge("session_123", {
          readHistory: false,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 10,
        });

        expect(bridge.getContext("unknown")).toBeUndefined();
      });

      it("setContext does nothing when writeContext is false", () => {
        const bridge = factory.createBridge("session_123", {
          readHistory: false,
          readPellets: false,
          writeContext: false,
          maxHistoryDepth: 10,
        });

        bridge.setContext("key", "value");
        expect(bridge.getContext("key")).toBeUndefined();
      });

      it("setContext stores value when writeContext is true", () => {
        const bridge = factory.createBridge("session_123", {
          readHistory: false,
          readPellets: false,
          writeContext: true,
          maxHistoryDepth: 10,
        });

        bridge.setContext("key", "value");
        expect(bridge.getContext("key")).toBe("value");
      });
    });

    describe("metadata", () => {
      it("returns correct metadata", () => {
        const permissions = {
          readHistory: true,
          readPellets: true,
          writeContext: false,
          maxHistoryDepth: 20,
        };
        const bridge = factory.createBridge("session_xyz", permissions);

        expect(bridge.metadata).toEqual({
          sessionId: "session_xyz",
          readHistory: true,
          readPellets: true,
          writeContext: false,
          maxHistoryDepth: 20,
        });
      });
    });
  });
});

describe("ACPRouter", () => {
  let router: ACPRouter;
  let eventBus: EventBus;
  let agentRegistry: AgentRegistry;

  beforeEach(() => {
    eventBus = makeMockEventBus();
    agentRegistry = makeMockAgentRegistry();
    router = new ACPRouter(agentRegistry, eventBus, undefined, 100);
  });

  describe("registerAgent()", () => {
    it("registers agent with capabilities and handlers", () => {
      const capabilities: ACPCapability[] = [
        {
          name: "code-review",
          channels: ["review"],
          concurrency: 2,
          priority: 1,
        },
      ];
      const handler: ACPMessageHandler = vi.fn().mockResolvedValue("result");

      router.registerAgent("agent1", capabilities, [
        { channel: "review", handler },
      ]);

      const caps = router.listCapabilities();
      expect(caps).toHaveLength(1);
      expect(caps[0].agentId).toBe("agent1");
      expect(caps[0].capabilities).toEqual(capabilities);
    });

    it("registers handler on correct channel", () => {
      const handler: ACPMessageHandler = vi.fn();
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      expect(router.findByChannel("chat")).toEqual(["agent1"]);
      expect(router.findByChannel("unknown")).toEqual([]);
    });

    it("allows multiple agents on same channel", () => {
      const handler1: ACPMessageHandler = vi.fn();
      const handler2: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [],
        [{ channel: "chat", handler: handler1 }],
      );
      router.registerAgent(
        "agent2",
        [],
        [{ channel: "chat", handler: handler2 }],
      );

      expect(router.findByChannel("chat")).toContain("agent1");
      expect(router.findByChannel("chat")).toContain("agent2");
    });
  });

  describe("unregisterAgent()", () => {
    it("removes agent and clears its handlers", () => {
      const handler: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [{ name: "test", channels: ["t"], concurrency: 1, priority: 1 }],
        [{ channel: "t", handler }],
      );

      router.unregisterAgent("agent1");

      expect(router.listCapabilities()).toHaveLength(0);
      expect(router.findByChannel("t")).toEqual([]);
    });

    it("does not affect other agents", () => {
      const handler1: ACPMessageHandler = vi.fn();
      const handler2: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [],
        [{ channel: "chat", handler: handler1 }],
      );
      router.registerAgent(
        "agent2",
        [],
        [{ channel: "chat", handler: handler2 }],
      );

      router.unregisterAgent("agent1");

      expect(router.findByChannel("chat")).toEqual(["agent2"]);
    });
  });

  describe("send()", () => {
    it("returns not-found when recipient agent does not exist", async () => {
      const msg = makeMessage({ to: "nonexistent" });
      const status = await router.send(msg);
      expect(status).toBe("not-found");
    });

    it("returns not-found when channel does not exist for agent", async () => {
      const handler: ACPMessageHandler = vi.fn();
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const msg = makeMessage({ to: "agent1", channel: "unknown-channel" });
      const status = await router.send(msg);
      expect(status).toBe("not-found");
    });

    it("delivers message to registered handler", async () => {
      const handler: ACPMessageHandler = vi.fn().mockResolvedValue("handled");
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const msg = makeMessage({
        to: "agent1",
        channel: "chat",
        payload: { data: "test" },
      });
      const status = await router.send(msg);

      expect(status).toBe("delivered");
      expect(handler).toHaveBeenCalledWith(msg, undefined);
    });

    it("returns rejected when handler throws", async () => {
      const handler: ACPMessageHandler = vi
        .fn()
        .mockRejectedValue(new Error("Handler failed"));
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const msg = makeMessage({ to: "agent1", channel: "chat" });
      const status = await router.send(msg);

      expect(status).toBe("rejected");
    });

    it("emits acp:message:delivered event on success", async () => {
      const handler: ACPMessageHandler = vi.fn().mockResolvedValue("ok");
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const msg = makeMessage({ to: "agent1", channel: "chat" });
      await router.send(msg);

      expect(eventBus.emit).toHaveBeenCalledWith(
        "acp:message:delivered",
        expect.objectContaining({
          messageId: msg.id,
          from: msg.from,
          to: msg.to,
          channel: msg.channel,
        }),
      );
    });

    it("emits acp:message:failed event on handler error", async () => {
      const handler: ACPMessageHandler = vi
        .fn()
        .mockRejectedValue(new Error("fail"));
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const msg = makeMessage({ to: "agent1", channel: "chat" });
      await router.send(msg);

      expect(eventBus.emit).toHaveBeenCalledWith(
        "acp:message:failed",
        expect.objectContaining({
          messageId: msg.id,
          from: msg.from,
          to: msg.to,
        }),
      );
    });
  });

  describe("sendToCapability()", () => {
    it("returns not-found when no agent has the capability", async () => {
      const result = await router.sendToCapability("nonexistent-cap", {
        data: "test",
      });
      expect(result.status).toBe("not-found");
      expect(result.agentId).toBe("");
    });

    it("routes to agent with matching capability", async () => {
      const handler: ACPMessageHandler = vi.fn().mockResolvedValue("ok");
      router.registerAgent(
        "agent1",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 2,
          },
        ],
        [{ channel: "review", handler }],
      );

      const result = await router.sendToCapability(
        "code-review",
        { data: "test" },
        { channel: "review" },
      );

      expect(result.status).toBe("delivered");
      expect(result.agentId).toBe("agent1");
    });

    it("prefers agent specified in prefer option", async () => {
      const handler1: ACPMessageHandler = vi.fn();
      const handler2: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 1,
          },
        ],
        [{ channel: "review", handler: handler1 }],
      );
      router.registerAgent(
        "agent2",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 1,
          },
        ],
        [{ channel: "review", handler: handler2 }],
      );

      const result = await router.sendToCapability(
        "code-review",
        { data: "test" },
        { prefer: "agent2" },
      );

      expect(result.agentId).toBe("agent2");
    });

    it("excludes agents specified in exclude option", async () => {
      const handler1: ACPMessageHandler = vi.fn();
      const handler2: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 1,
          },
        ],
        [{ channel: "review", handler: handler1 }],
      );
      router.registerAgent(
        "agent2",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 1,
          },
        ],
        [{ channel: "review", handler: handler2 }],
      );

      const result = await router.sendToCapability(
        "code-review",
        { data: "test" },
        { exclude: ["agent1"] },
      );

      expect(result.agentId).toBe("agent2");
    });

    it("selects lowest priority when multiple candidates", async () => {
      const handler1: ACPMessageHandler = vi.fn();
      const handler2: ACPMessageHandler = vi.fn();
      router.registerAgent(
        "agent1",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 5,
          },
        ],
        [{ channel: "review", handler: handler1 }],
      );
      router.registerAgent(
        "agent2",
        [
          {
            name: "code-review",
            channels: ["review"],
            concurrency: 1,
            priority: 1,
          },
        ],
        [{ channel: "review", handler: handler2 }],
      );

      const result = await router.sendToCapability("code-review", {
        data: "test",
      });

      expect(result.agentId).toBe("agent2");
    });
  });

  describe("request()", () => {
    it("rejects on timeout", async () => {
      vi.useFakeTimers();

      const handler: ACPMessageHandler = vi
        .fn()
        .mockImplementation(() => new Promise(() => {}));
      router.registerAgent("agent1", [], [{ channel: "chat", handler }]);

      const requestPromise = router.request(
        "agent1",
        "chat",
        { query: "test" },
        50,
      );

      vi.advanceTimersByTime(60);

      await expect(requestPromise).rejects.toThrow(/timed out/);

      vi.useRealTimers();
    });
  });

  describe("openStream()", () => {
    it("returns a stream writer with write and end methods", () => {
      const writer = router.openStream("agent1", "chat");

      expect(typeof writer.write).toBe("function");
      expect(typeof writer.end).toBe("function");
      expect(typeof writer.error).toBe("function");
    });

    it("emits stream:opened event", () => {
      router.openStream("agent1", "chat");

      expect(eventBus.emit).toHaveBeenCalledWith(
        "acp:stream:opened",
        expect.objectContaining({
          to: "agent1",
          channel: "chat",
        }),
      );
    });

    it("write and end methods do not throw", () => {
      const writer = router.openStream("agent1", "chat");

      expect(() => writer.write({ data: "test" })).not.toThrow();
      expect(() => writer.end()).not.toThrow();
      expect(() => writer.error(new Error("test"))).not.toThrow();
    });
  });

  describe("listCapabilities()", () => {
    it("returns empty array when no agents registered", () => {
      expect(router.listCapabilities()).toEqual([]);
    });

    it("returns all registered capabilities", () => {
      router.registerAgent(
        "agent1",
        [
          { name: "cap1", channels: ["ch1"], concurrency: 1, priority: 1 },
          { name: "cap2", channels: ["ch2"], concurrency: 2, priority: 2 },
        ],
        [],
      );

      const caps = router.listCapabilities();
      expect(caps).toHaveLength(1);
      expect(caps[0].capabilities).toHaveLength(2);
    });
  });

  describe("findByChannel()", () => {
    it("returns empty array for unknown channel", () => {
      expect(router.findByChannel("unknown")).toEqual([]);
    });

    it("finds all agents on a channel", () => {
      router.registerAgent(
        "agent1",
        [],
        [{ channel: "chat", handler: vi.fn() }],
      );
      router.registerAgent(
        "agent2",
        [],
        [{ channel: "chat", handler: vi.fn() }],
      );

      const agents = router.findByChannel("chat");
      expect(agents).toContain("agent1");
      expect(agents).toContain("agent2");
    });
  });
});
