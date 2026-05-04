import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { MemoryWriter } from "../src/memory/writer.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";
import { GatewayEventBus, type GatewaySystemEvent } from "../src/gateway/event-bus.js";

interface StubChatResponse {
  content: string;
}

function makeStubs(chatResponse?: StubChatResponse | (() => Promise<StubChatResponse>)) {
  const chatMock = vi.fn().mockImplementation(async () => {
    if (typeof chatResponse === "function") return chatResponse();
    return chatResponse ?? { content: JSON.stringify({ extractions: [] }) };
  });
  const provider = { name: "stub", chat: chatMock };
  const providerRegistry = {
    get: vi.fn().mockReturnValue(provider),
  };
  const router = {
    resolve: vi.fn().mockReturnValue({
      provider: "stub",
      model: "stub-cheap",
      tier: "low",
    }),
  };
  return { chatMock, providerRegistry, router };
}

function makeWriter(extra?: { chatResponse?: StubChatResponse | (() => Promise<StubChatResponse>) }) {
  const db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  applyV25Migration(db);
  const bus = new GatewayEventBus();
  const repo = new MemoryRepository(db, bus);
  const stubs = makeStubs(extra?.chatResponse);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const writer = new MemoryWriter({ repo, bus, router: stubs.router as any, providerRegistry: stubs.providerRegistry as any });
  return { db, bus, repo, writer, ...stubs };
}

describe("MemoryWriter — trivial turns", () => {
  it("skips empty user messages without invoking the router", async () => {
    const { writer, router } = makeWriter();
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
    const { writer, router } = makeWriter();
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
    const { writer } = makeWriter();
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
    const { writer } = makeWriter();
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

describe("MemoryWriter.classify (cheap-tier)", () => {
  it("uses IntelligenceRouter.resolve('classification')", async () => {
    const { writer, router } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [
            { kind: "semantic", content: "user prefers TypeScript over JS", importance: 0.7 },
          ],
        }),
      },
    });
    await writer.ingest({
      sessionId: "s1",
      turnId: "t1",
      channel: "cli",
      userMessage: "I always prefer TypeScript over JavaScript for new projects.",
      assistantResponse: "Got it.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(router.resolve).toHaveBeenCalledWith("classification");
  });

  it("looks up provider via providerRegistry by resolved name", async () => {
    const { writer, providerRegistry } = makeWriter({
      chatResponse: {
        content: JSON.stringify({ extractions: [] }),
      },
    });
    await writer.ingest({
      sessionId: "s1",
      turnId: "t-lookup",
      channel: "cli",
      userMessage: "I always prefer TypeScript.",
      assistantResponse: "Noted.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(providerRegistry.get).toHaveBeenCalledWith("stub");
  });

  it("calls provider.chat with the resolved model", async () => {
    const { writer, chatMock } = makeWriter({
      chatResponse: { content: JSON.stringify({ extractions: [] }) },
    });
    await writer.ingest({
      sessionId: "s1",
      turnId: "t-model",
      channel: "cli",
      userMessage: "I always prefer TypeScript.",
      assistantResponse: "Noted.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(chatMock).toHaveBeenCalled();
    const call = chatMock.mock.calls[0];
    expect(call[1]).toBe("stub-cheap");
  });

  it("short-circuits on empty extraction (no DB writes)", async () => {
    const { writer, repo } = makeWriter({
      chatResponse: { content: JSON.stringify({ extractions: [] }) },
    });
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t2",
      channel: "cli",
      userMessage: "Just thinking out loud about something abstract.",
      assistantResponse: "Sure.",
      verdict: "NEUTRAL",
      goalId: null,
      subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(r.reason).toBe("empty-extraction");
    expect(repo.stats().total).toBe(0);
  });

  it("persists extracted records via repository", async () => {
    const { writer, repo } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [
            { kind: "semantic", content: "user prefers TypeScript", importance: 0.7 },
            { kind: "episodic", content: "user is refactoring memory store today", importance: 0.5 },
          ],
        }),
      },
    });
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t3",
      channel: "cli",
      userMessage: "I always prefer TypeScript and I'm working on the memory store today.",
      assistantResponse: "Got it.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(false);
    expect(r.written).toBe(2);
    const stats = repo.stats();
    expect(stats.total).toBe(2);
    expect(stats.byKind.semantic).toBe(1);
    expect(stats.byKind.episodic).toBe(1);
  });

  it("clamps importance into [0,1]", async () => {
    const { writer, repo } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [
            { kind: "semantic", content: "x", importance: 5 },
            { kind: "semantic", content: "y", importance: -2 },
          ],
        }),
      },
    });
    await writer.ingest({
      sessionId: "s1",
      turnId: "t-clamp",
      channel: "cli",
      userMessage: "long enough message to bypass trivial-turn guard",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    const stats = repo.stats();
    expect(stats.total).toBe(2);
    expect(stats.avgImportance).toBeCloseTo(0.5, 5);
  });

  it("propagates goal_id, subgoal_id, verdict, source_turn_id, source_channel", async () => {
    const { writer, repo } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "p", importance: 0.5 }],
        }),
      },
    });
    await writer.ingest({
      sessionId: "s1",
      turnId: "turn-xyz",
      channel: "telegram",
      userMessage: "I always prefer TypeScript.",
      assistantResponse: "Noted.",
      verdict: "PARTIAL",
      goalId: "g-99",
      subGoalId: "sg-7",
    });
    const all = await repo.search("anything", { topK: 1 });
    const rec = all[0];
    expect(rec.goal_id).toBe("g-99");
    expect(rec.subgoal_id).toBe("sg-7");
    expect(rec.verdict).toBe("PARTIAL");
    expect(rec.source_turn_id).toBe("turn-xyz");
    expect(rec.source_channel).toBe("telegram");
  });

  it("emits memory:classify_failed on chat error and returns classify-failed", async () => {
    const { writer, bus } = makeWriter({
      chatResponse: () => Promise.reject(new Error("provider down")),
    });
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:classify_failed", (e) => captured.push(e));
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-fail",
      channel: "cli",
      userMessage: "I always prefer TypeScript.",
      assistantResponse: "Noted.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(r.reason).toBe("classify-failed");
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({
      type: "memory:classify_failed",
      turnId: "t-fail",
    });
  });

  it("emits memory:classify_failed on invalid JSON response", async () => {
    const { writer, bus } = makeWriter({
      chatResponse: { content: "not json at all" },
    });
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:classify_failed", (e) => captured.push(e));
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-bad-json",
      channel: "cli",
      userMessage: "I always prefer TypeScript.",
      assistantResponse: "Noted.",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.reason).toBe("classify-failed");
    expect(captured).toHaveLength(1);
  });
});
