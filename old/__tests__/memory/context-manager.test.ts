import { describe, it, expect, beforeEach } from "vitest";
import { ContextManager } from "../../src/memory/context-manager.js";
import type { ChatMessage } from "../../src/providers/base.js";

function makeMessage(
  role: "user" | "assistant" | "system",
  content: string,
): ChatMessage {
  return { role, content };
}

describe("ContextManager", () => {
  let manager: ContextManager;

  beforeEach(() => {
    manager = new ContextManager();
  });

  describe("getOrCreate()", () => {
    it("creates a new context for unknown session", () => {
      const ctx = manager.getOrCreate("new-session");
      expect(ctx.messages).toHaveLength(0);
      expect(ctx.totalTokens).toBe(0);
      expect(ctx.messageCount).toBe(0);
    });

    it("returns existing context for known session", () => {
      const ctx1 = manager.getOrCreate("session-1");
      ctx1.messages.push(makeMessage("user", "Hello"));

      const ctx2 = manager.getOrCreate("session-1");
      expect(ctx2.messages).toHaveLength(1);
    });

    it("creates separate contexts for different sessions", () => {
      manager.getOrCreate("session-1").messages.push(makeMessage("user", "Hello"));
      manager.getOrCreate("session-2").messages.push(makeMessage("user", "World"));

      expect(manager.getMessages("session-1")).toHaveLength(1);
      expect(manager.getMessages("session-2")).toHaveLength(1);
    });
  });

  describe("addMessage()", () => {
    it("adds a message to the session context", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));
      manager.addMessage("session-1", makeMessage("assistant", "Hi there"));

      expect(manager.getMessageCount("session-1")).toBe(2);
    });

    it("updates token count", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello world"));

      const tokens = manager.getTokenCount("session-1");
      expect(tokens).toBeGreaterThan(0);
    });

    it("returns truncation result when within limits", () => {
      const result = manager.addMessage("session-1", makeMessage("user", "Hello"));

      expect(result.wasTruncated).toBe(false);
      expect(result.truncationPoint).toBe("none");
    });
  });

  describe("addMessages()", () => {
    it("adds multiple messages at once", () => {
      manager.addMessages("session-1", [
        makeMessage("user", "Hello"),
        makeMessage("assistant", "Hi there"),
        makeMessage("user", "How are you?"),
      ]);

      expect(manager.getMessageCount("session-1")).toBe(3);
    });
  });

  describe("getMessages()", () => {
    it("returns all messages for a session", () => {
      manager.addMessages("session-1", [
        makeMessage("user", "First"),
        makeMessage("assistant", "Second"),
      ]);

      const messages = manager.getMessages("session-1");
      expect(messages).toHaveLength(2);
      expect(messages[0].content).toBe("First");
      expect(messages[1].content).toBe("Second");
    });

    it("returns empty array for unknown session", () => {
      const messages = manager.getMessages("unknown");
      expect(messages).toHaveLength(0);
    });
  });

  describe("getRecentMessages()", () => {
    it("returns recent messages within token limit", () => {
      manager.addMessages("session-1", [
        makeMessage("user", "Short"),
        makeMessage("user", "A somewhat longer message that contains more content"),
        makeMessage("user", "Another short"),
      ]);

      const recent = manager.getRecentMessages("session-1", 50);
      expect(recent.length).toBeGreaterThan(0);
      expect(recent.length).toBeLessThanOrEqual(3);
    });

    it("returns all messages when no token limit specified", () => {
      manager.addMessages("session-1", [
        makeMessage("user", "First"),
        makeMessage("user", "Second"),
      ]);

      const recent = manager.getRecentMessages("session-1");
      expect(recent).toHaveLength(2);
    });
  });

  describe("clear()", () => {
    it("clears context for a specific session", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));
      manager.addMessage("session-2", makeMessage("user", "World"));

      manager.clear("session-1");

      expect(manager.getMessages("session-1")).toHaveLength(0);
      expect(manager.getMessages("session-2")).toHaveLength(1);
    });
  });

  describe("clearAll()", () => {
    it("clears all contexts", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));
      manager.addMessage("session-2", makeMessage("user", "World"));

      manager.clearAll();

      expect(manager.getMessages("session-1")).toHaveLength(0);
      expect(manager.getMessages("session-2")).toHaveLength(0);
    });
  });

  describe("has()", () => {
    it("returns true for known session", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));
      expect(manager.has("session-1")).toBe(true);
    });

    it("returns false for unknown session", () => {
      expect(manager.has("unknown")).toBe(false);
    });
  });

  describe("getSessionInfo()", () => {
    it("returns session metadata", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));

      const info = manager.getSessionInfo("session-1");
      expect(info).not.toBeNull();
      expect(info!.messageCount).toBe(1);
      expect(info!.tokenCount).toBeGreaterThan(0);
      expect(info!.oldestTimestamp).toBeDefined();
      expect(info!.newestTimestamp).toBeDefined();
    });

    it("returns null for unknown session", () => {
      expect(manager.getSessionInfo("unknown")).toBeNull();
    });
  });

  describe("buildContextString()", () => {
    it("returns formatted context string", () => {
      manager.addMessages("session-1", [
        makeMessage("user", "Hello"),
        makeMessage("assistant", "Hi there"),
      ]);

      const context = manager.buildContextString("session-1");
      expect(context).toContain("user: Hello");
      expect(context).toContain("assistant: Hi there");
    });

    it("returns empty string for unknown session", () => {
      const context = manager.buildContextString("unknown");
      expect(context).toBe("");
    });

    it("includes metadata when requested", () => {
      manager.addMessage("session-1", makeMessage("user", "Hello"));

      const context = manager.buildContextString("session-1", { includeMetadata: true });
      expect(context).toContain("Session context");
    });
  });

  describe("window enforcement", () => {
    it("respects maxMessages limit", () => {
      const limitedManager = new ContextManager({ maxMessages: 3 });

      for (let i = 0; i < 5; i++) {
        limitedManager.addMessage("session-1", makeMessage("user", `Message ${i}`));
      }

      expect(limitedManager.getMessageCount("session-1")).toBeLessThanOrEqual(3);
    });

    it("respects maxTokens limit", () => {
      const limitedManager = new ContextManager({ maxTokens: 50 });

      for (let i = 0; i < 3; i++) {
        limitedManager.addMessage("session-1", makeMessage("user", "A longer message with more content here"));
      }

      const result = limitedManager.getTokenCount("session-1");
      expect(result).toBeLessThanOrEqual(100);
    });

    it("returns truncation result when truncating", () => {
      const limitedManager = new ContextManager({ maxMessages: 2 });

      limitedManager.addMessage("session-1", makeMessage("user", "First"));
      limitedManager.addMessage("session-1", makeMessage("user", "Second"));
      const result = limitedManager.addMessage("session-1", makeMessage("user", "Third"));

      expect(result.wasTruncated).toBe(true);
      expect(result.removedCount).toBeGreaterThan(0);
      expect(result.truncationPoint).toBe("tokens");
    });
  });
});
