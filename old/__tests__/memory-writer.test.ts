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

/**
 * Sequential chat stub. Each call returns the next response in the queue.
 * Useful for ingest flows that call the LLM twice (classify, then reconcile).
 */
function makeSequentialStubs(responses: StubChatResponse[]) {
  let i = 0;
  const chatMock = vi.fn().mockImplementation(async () => {
    const r = responses[i] ?? responses[responses.length - 1];
    i += 1;
    return r;
  });
  const provider = { name: "stub", chat: chatMock };
  const providerRegistry = { get: vi.fn().mockReturnValue(provider) };
  const router = {
    resolve: vi.fn().mockReturnValue({
      provider: "stub",
      model: "stub-cheap",
      tier: "low",
    }),
  };
  return { chatMock, providerRegistry, router };
}

function makeWriterSequential(responses: StubChatResponse[]) {
  const db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  applyV25Migration(db);
  const bus = new GatewayEventBus();
  const repo = new MemoryRepository(db, bus);
  const stubs = makeSequentialStubs(responses);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const writer = new MemoryWriter({ repo, bus, router: stubs.router as any, providerRegistry: stubs.providerRegistry as any });
  return { db, bus, repo, writer, ...stubs };
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

describe("MemoryWriter.reconcile", () => {
  it("inserts candidate when no similar memories exist (auto-ADD)", async () => {
    const { writer, repo } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "novel fact about user", importance: 0.6 }],
        }),
      },
    });
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-novel",
      channel: "cli",
      userMessage: "novel fact about user that has no precedent in memory",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(false);
    expect(r.written).toBe(1);
    expect(repo.stats().total).toBe(1);
  });

  it("DELETE invalidates target and emits memory:contradiction_detected, no insert", async () => {
    const { writer, bus, repo } = makeWriterSequential([
      // Seed-ingest classify
      {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers Python", importance: 0.7 }],
        }),
      },
      // Second-ingest classify
      {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }],
        }),
      },
      // Reconciler decision: DELETE existing (the Python one)
      {
        content: JSON.stringify({
          decisions: [
            {
              action: "DELETE",
              target_id: "__placeholder__",
              reason: "user changed preference",
            },
          ],
        }),
      },
    ]);

    // Seed an existing memory via the writer (simpler than crafting a candidates row).
    await writer.ingest({
      sessionId: "s1",
      turnId: "t-seed",
      channel: "cli",
      userMessage: "I prefer Python over TypeScript for new projects",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    // Look up the seeded memory's id and patch the reconciler decision queue.
    const seeded = (await repo.search("python", { topK: 1 }))[0];
    expect(seeded).toBeDefined();
    // Patch the placeholder in the queued reconciler response.
    // chatMock pushes responses in order, so the third one is reconciler.
    // We exposed the responses array via closure in makeSequentialStubs — instead
    // we just validate by intercepting the event payload.

    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:contradiction_detected", (e) => captured.push(e));

    // The reconciler stub still has placeholder target_id, so DELETE will resolve to
    // a non-existent id. To make this end-to-end, swap the third response by re-issuing
    // the writer with a fresh sequential stub that knows the real id.
    const fresh = makeWriterSequential([
      {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }],
        }),
      },
      {
        content: JSON.stringify({
          decisions: [
            {
              action: "DELETE",
              target_id: seeded.id,
              reason: "user changed preference",
            },
          ],
        }),
      },
    ]);
    // Reseed in fresh DB.
    fresh.repo.insertBatch([
      {
        id: seeded.id,
        kind: "semantic",
        content: seeded.content,
        importance: seeded.importance,
      },
    ]);
    const captured2: GatewaySystemEvent[] = [];
    fresh.bus.on("memory:contradiction_detected", (e) => captured2.push(e));

    const r = await fresh.writer.ingest({
      sessionId: "s1",
      turnId: "t-update",
      channel: "cli",
      userMessage: "Actually I prefer TypeScript now",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.invalidated).toBe(1);
    expect(r.written ?? 0).toBe(0);
    const seededAfter = fresh.repo.getById(seeded.id);
    expect(seededAfter?.invalid_at).not.toBeNull();
    expect(captured2).toHaveLength(1);
    expect(captured2[0]).toMatchObject({
      type: "memory:contradiction_detected",
      memoryId: seeded.id,
    });
  });

  it("UPDATE invalidates target AND inserts new memory", async () => {
    const seedId = "mem-update-target";
    const { writer, repo } = makeWriterSequential([
      {
        content: JSON.stringify({
          extractions: [
            { kind: "semantic", content: "user prefers TypeScript strict mode", importance: 0.7 },
          ],
        }),
      },
      {
        content: JSON.stringify({
          decisions: [{ action: "UPDATE", target_id: seedId, reason: "refines existing" }],
        }),
      },
    ]);
    repo.insertBatch([
      {
        id: seedId,
        kind: "semantic",
        content: "user prefers TypeScript",
        importance: 0.6,
      },
    ]);
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-refine",
      channel: "cli",
      userMessage: "I always use TypeScript with strict mode",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(false);
    expect(r.invalidated).toBe(1);
    expect(r.written).toBe(1);
    const old = repo.getById(seedId);
    expect(old?.invalid_at).not.toBeNull();
    const live = await repo.search("typescript strict", { topK: 5 });
    const fresh = live.find((m) => m.id !== seedId);
    expect(fresh).toBeDefined();
  });

  it("NOOP does not insert and returns skipped=noop", async () => {
    const seedId = "mem-noop-target";
    const { writer, repo } = makeWriterSequential([
      {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }],
        }),
      },
      {
        content: JSON.stringify({
          decisions: [{ action: "NOOP", target_id: seedId, reason: "duplicate" }],
        }),
      },
    ]);
    repo.insertBatch([
      {
        id: seedId,
        kind: "semantic",
        content: "user prefers TypeScript",
        importance: 0.6,
      },
    ]);
    const before = repo.stats().total;
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-dup",
      channel: "cli",
      userMessage: "I prefer TypeScript",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(r.reason).toBe("noop");
    expect(repo.stats().total).toBe(before);
  });

  it("falls back to ADD when reconciler crashes (fail-open)", async () => {
    const seedId = "mem-fail-target";
    const { writer, bus, repo } = makeWriterSequential([
      {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers Rust", importance: 0.6 }],
        }),
      },
      // reconciler returns garbage → crashes → fail open
      { content: "not json at all" },
    ]);
    repo.insertBatch([
      {
        id: seedId,
        kind: "semantic",
        content: "user prefers Rust",
        importance: 0.6,
      },
    ]);
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:contradict_failed", (e) => captured.push(e));
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-fail-open",
      channel: "cli",
      userMessage: "I love Rust for systems work",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(false);
    expect(r.written).toBe(1);
    expect(captured).toHaveLength(1);
  });
});

describe("MemoryWriter — engine:turn_complete listener", () => {
  it("expires working memories older than 24h on engine:turn_complete", async () => {
    const { writer, bus, repo, db } = makeWriter();
    writer.attachBusListeners();
    const oldTs = new Date(Date.now() - 25 * 3600_000).toISOString();
    repo.insertBatch([
      {
        id: "old-working",
        kind: "working",
        content: "stale working memory",
        importance: 0.4,
        valid_at: oldTs,
      },
      {
        id: "fresh-working",
        kind: "working",
        content: "fresh working memory",
        importance: 0.4,
      },
    ]);
    bus.emit({ type: "engine:turn_complete", sessionId: "s1" });
    // Listener is sync — assert immediately.
    const oldRow = db
      .prepare(`SELECT invalid_at FROM memories WHERE id = ?`)
      .get("old-working") as { invalid_at: string | null };
    const freshRow = db
      .prepare(`SELECT invalid_at FROM memories WHERE id = ?`)
      .get("fresh-working") as { invalid_at: string | null };
    expect(oldRow.invalid_at).not.toBeNull();
    expect(freshRow.invalid_at).toBeNull();
  });

  it("emits memory:write_failed when expire throws", async () => {
    const { writer, bus, db } = makeWriter();
    writer.attachBusListeners();
    db.close();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:write_failed", (e) => captured.push(e));
    bus.emit({ type: "engine:turn_complete", sessionId: "s1" });
    expect(captured).toHaveLength(1);
  });
});

describe("MemoryWriter.recordReflexive", () => {
  it("inserts a reflexive-kind memory with default importance", async () => {
    const { writer, repo } = makeWriter();
    await writer.recordReflexive({
      sessionId: "s1",
      observation: "engine self-noticed it skipped 3 trivial turns in a row",
    });
    const stats = repo.stats();
    expect(stats.total).toBe(1);
    expect(stats.byKind.reflexive).toBe(1);
    const all = await repo.search("engine self", { kinds: ["reflexive"], topK: 1 });
    expect(all[0].content).toContain("engine self-noticed");
    expect(all[0].importance).toBeCloseTo(0.5, 5);
    expect(all[0].source_channel).toBe("engine-reflexive");
  });

  it("respects custom importance and goalId", async () => {
    const { writer, repo } = makeWriter();
    await writer.recordReflexive({
      sessionId: "s1",
      observation: "engine noted goal completion",
      importance: 0.85,
      goalId: "g-42",
    });
    const r = (await repo.search("goal completion", { kinds: ["reflexive"], topK: 1 }))[0];
    expect(r.importance).toBeCloseTo(0.85, 5);
    expect(r.goal_id).toBe("g-42");
  });
});

describe("MemoryWriter — write-failed envelope", () => {
  it("emits memory:write_failed and returns skipped=write-failed when insertBatch throws", async () => {
    const { writer, bus, repo } = makeWriter({
      chatResponse: {
        content: JSON.stringify({
          extractions: [{ kind: "semantic", content: "user prefers Go", importance: 0.6 }],
        }),
      },
    });
    // Force insertBatch to throw.
    const insertSpy = vi.spyOn(repo, "insertBatch").mockImplementation(() => {
      throw new Error("disk full");
    });
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:write_failed", (e) => captured.push(e));
    const r = await writer.ingest({
      sessionId: "s1",
      turnId: "t-fail-write",
      channel: "cli",
      userMessage: "I really like Go for distributed systems work",
      assistantResponse: "ok",
      verdict: "ADVANCES",
      goalId: "g1",
      subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(r.reason).toBe("write-failed");
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ type: "memory:write_failed" });
    insertSpy.mockRestore();
  });
});
