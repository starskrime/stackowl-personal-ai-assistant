import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { SessionService } from "../src/session/service.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

function makeMockCompressor() {
  return {
    compress: vi.fn().mockResolvedValue(undefined),
    buildContext: vi.fn().mockReturnValue(""),
  };
}

function makeMockUserMemoryStore() {
  return {
    retrieve: vi.fn().mockResolvedValue([]),
    add: vi.fn().mockResolvedValue(undefined),
  };
}

function makeMockRegistry() {
  return {
    get: vi.fn().mockReturnValue({
      chat: vi.fn().mockResolvedValue({ content: "[]" }),
    }),
  };
}

describe("SessionService", () => {
  let tmpDir: string;
  let db: MemoryDatabase;
  let svc: SessionService;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "owl-svc-"));
    db = new MemoryDatabase(tmpDir);
    svc = new SessionService(
      db,
      makeMockCompressor() as any,
      makeMockUserMemoryStore() as any,
      undefined,
      makeMockRegistry() as any,
      "openai",
      "gpt-4o-mini",
    );
  });

  afterEach(() => {
    svc.destroy();
    db.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  describe("getOrCreate()", () => {
    it("creates a new session when none exists", async () => {
      const session = await svc.getOrCreate("cli:user1", "user1", "hoot");
      expect(session.id).toBe("cli:user1");
      expect(session.messages).toBeInstanceOf(Array);
    });

    it("returns cached session on second call", async () => {
      const s1 = await svc.getOrCreate("cli:user1", "user1", "hoot");
      const s2 = await svc.getOrCreate("cli:user1", "user1", "hoot");
      expect(s1).toBe(s2); // same reference
    });
  });

  describe("addMessages()", () => {
    it("appends messages to SQLite", async () => {
      await svc.getOrCreate("cli:user1", "user1", "hoot");
      await svc.addMessages("cli:user1", [
        { role: "user", content: "hello" },
        { role: "assistant", content: "hi" },
      ]);
      expect(db.messages.countSession("cli:user1")).toBe(2);
    });
  });

  describe("buildContext()", () => {
    it("returns SessionContext with recentMessages array", async () => {
      await svc.getOrCreate("cli:user1", "user1", "hoot");
      await svc.addMessages("cli:user1", [{ role: "user", content: "test" }]);
      const ctx = await svc.buildContext("cli:user1", "user1", "test");
      expect(ctx).toHaveProperty("summaryBlock");
      expect(ctx).toHaveProperty("recentFacts");
      expect(ctx).toHaveProperty("recentMessages");
      expect(Array.isArray(ctx.recentMessages)).toBe(true);
    });
  });

  describe("isGreetingPattern()", () => {
    it.each([
      ["hi there", true],
      ["hello!", true],
      ["hey", true],
      ["good morning everyone", true],
      ["what time is it?", false],
      ["", false],
      ["ship it", false],
    ])('isGreetingPattern("%s") === %s', (text, expected) => {
      expect(SessionService.isGreetingPattern(text)).toBe(expected);
    });
  });

  describe("evictStale()", () => {
    it("returns empty array when no stale sessions", async () => {
      await svc.getOrCreate("cli:user1", "user1", "hoot");
      const evicted = svc.evictStale();
      expect(evicted).toEqual([]);
    });
  });

  describe("getUserId()", () => {
    it("returns userId for cached session", async () => {
      await svc.getOrCreate("cli:user1", "user1", "hoot");
      expect(svc.getUserId("cli:user1")).toBe("user1");
    });

    it("returns undefined for unknown session", () => {
      expect(svc.getUserId("unknown")).toBeUndefined();
    });
  });
});
