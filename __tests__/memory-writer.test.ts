import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { MemoryWriter } from "../src/memory/writer.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";

function makeStubRouter() {
  return {
    resolve: vi.fn().mockReturnValue({
      provider: { chat: vi.fn() },
      model: "stub",
      tier: "cheap",
    }),
  } as unknown as Parameters<typeof MemoryWriter.prototype.constructor>[0]["router"];
}

describe("MemoryWriter — trivial turns", () => {
  let db: Database.Database;
  let repo: MemoryRepository;
  let bus: GatewayEventBus;
  let writer: MemoryWriter;
  let router: ReturnType<typeof makeStubRouter>;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    router = makeStubRouter();
    writer = new MemoryWriter({ repo, bus, router });
  });

  it("skips empty user messages without invoking the router", async () => {
    const result = await writer.ingest({
      sessionId: "s1",
      turnId: "t1",
      channel: "cli",
      userMessage: "",
      assistantResponse: "ok",
      verdict: "NEUTRAL",
      goalId: null,
      subGoalId: null,
    });
    expect(result.skipped).toBe(true);
    expect(result.reason).toBe("trivial-turn");
    expect(router.resolve).not.toHaveBeenCalled();
  });

  it("skips short NEUTRAL turns without invoking the router", async () => {
    for (const msg of ["hi", "hello", "hey", "thanks", "ok", "thank you"]) {
      const r = await writer.ingest({
        sessionId: "s1",
        turnId: `t-${msg}`,
        channel: "cli",
        userMessage: msg,
        assistantResponse: "👋",
        verdict: "NEUTRAL",
        goalId: null,
        subGoalId: null,
      });
      expect(r.skipped).toBe(true);
    }
    expect(router.resolve).not.toHaveBeenCalled();
  });

  it("does not skip long NEUTRAL turns — those require LLM classification", async () => {
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-long",
      channel: "cli",
      userMessage: "I want to think about how to refactor the memory store later this week",
      assistantResponse: "noted",
      verdict: "NEUTRAL",
      goalId: null,
      subGoalId: null,
    });
    expect(r.reason).not.toBe("trivial-turn");
  });

  it("does not skip ADVANCES turns regardless of length", async () => {
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-short-advance",
      channel: "cli",
      userMessage: "go",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.reason).not.toBe("trivial-turn");
  });
});
