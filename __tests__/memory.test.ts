import { describe, it, expect, beforeEach, vi } from "vitest";
import { rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { EpisodicMemory, type Episode } from "../src/memory/episodic.js";
import {
  FactStore,
  type StoredFact,
  type FactCategory,
} from "../src/memory/fact-store.js";
import { SessionStore, type Session } from "../src/memory/store.js";
import { MemoryRetriever } from "../src/memory/memory-retriever.js";
import {
  findSegments,
  getSegmentMessages,
  getUnextractedSegments,
} from "../src/memory/session-segmenter.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { ChatMessage } from "../src/providers/base.js";
import type { KnowledgeGraph } from "../src/knowledge/graph.js";
import type { UserPreferenceModel } from "../src/preferences/model.js";
import type { PelletStore, Pellet } from "../src/pellets/store.js";

const testSpace = join(__dirname, ".test_memory_workspace");

async function cleanWorkspace() {
  await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  await mkdir(testSpace, { recursive: true });
}

function makeMockProvider(): ModelProvider {
  return {
    chat: vi.fn().mockResolvedValue({
      content: JSON.stringify({
        summary: "User asked about testing",
        keyFacts: ["User prefers vitest", "Testing is important"],
        topics: ["testing", "preferences"],
        sentiment: "positive",
      }),
    }),
    embed: vi.fn().mockResolvedValue({
      embedding: [0.1, 0.2, 0.3, 0.4, 0.5],
    }),
  } as unknown as ModelProvider;
}

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: "session_test_1",
    messages: [],
    metadata: {
      owlName: "TestOwl",
      startedAt: Date.now() - 3600000,
      lastUpdatedAt: Date.now(),
    },
    ...overrides,
  };
}

function makeMessage(
  role: "user" | "assistant" | "system",
  content: string,
  timestamp?: number,
): ChatMessage {
  return {
    role,
    content,
    ...(timestamp ? { timestamp } : {}),
  } as ChatMessage;
}

describe("SessionStore", () => {
  let store: SessionStore;

  beforeEach(async () => {
    await cleanWorkspace();
    store = new SessionStore(testSpace);
  });

  describe("createSession()", () => {
    it("creates a new session with correct metadata", () => {
      const session = store.createSession("Noctua");

      expect(session.id).toMatch(/^session_/);
      expect(session.metadata.owlName).toBe("Noctua");
      expect(session.messages).toHaveLength(0);
      expect(session.metadata.startedAt).toBeLessThanOrEqual(Date.now());
      expect(session.metadata.lastUpdatedAt).toBeGreaterThanOrEqual(
        session.metadata.startedAt,
      );
    });
  });

  describe("saveSession() and loadSession()", () => {
    it("saves and loads a session correctly", async () => {
      const session = makeSession({ id: "session_save_test" });
      session.messages = [
        makeMessage("user", "Hello"),
        makeMessage("assistant", "Hi there!"),
      ];

      await store.saveSession(session);
      const loaded = await store.loadSession("session_save_test");

      expect(loaded).not.toBeNull();
      expect(loaded!.id).toBe("session_save_test");
      expect(loaded!.messages).toHaveLength(2);
      expect(loaded!.metadata.owlName).toBe("TestOwl");
    });

    it("returns null for non-existent session", async () => {
      const result = await store.loadSession("nonexistent");
      expect(result).toBeNull();
    });
  });

  describe("listSessions()", () => {
    it("returns empty array when no sessions exist", async () => {
      await store.init();
      const sessions = await store.listSessions();
      expect(sessions).toHaveLength(0);
    });

    it("lists sessions sorted by most recent first", async () => {
      const s1 = makeSession({ id: "session_old" });
      s1.metadata.lastUpdatedAt = Date.now() - 86400000;
      const s2 = makeSession({ id: "session_new" });
      s2.metadata.lastUpdatedAt = Date.now();

      await store.saveSession(s1);
      await store.saveSession(s2);

      const sessions = await store.listSessions();
      expect(sessions).toHaveLength(2);
      expect(sessions[0].id).toBe("session_new");
      expect(sessions[1].id).toBe("session_old");
    });
  });

  describe("getRecentOrCreate()", () => {
    it("creates a new session when none exist", async () => {
      const session = await store.getRecentOrCreate("Noctua");
      expect(session.metadata.owlName).toBe("Noctua");
    });

    it("returns existing session if less than 12 hours old", async () => {
      const existing = makeSession({ id: "recent_session" });
      existing.metadata.lastUpdatedAt = Date.now() - 3600000; // 1 hour ago
      await store.saveSession(existing);

      const result = await store.getRecentOrCreate("TestOwl");

      expect(result.id).toBe("recent_session");
    });

    it("creates new session if most recent is older than 12 hours", async () => {
      const existing = makeSession({ id: "old_session" });
      await store.saveSession(existing);

      // Manually backdate the session file to 13 hours ago
      const filePath = join(testSpace, "sessions", "old_session.json");
      const { readFile, writeFile: write } = await import("node:fs/promises");
      const data = JSON.parse(await readFile(filePath, "utf-8"));
      data.metadata.lastUpdatedAt = Date.now() - 13 * 3600000;
      await write(filePath, JSON.stringify(data));

      const result = await store.getRecentOrCreate("TestOwl");

      expect(result.id).not.toBe("old_session");
    });
  });
});

describe("EpisodicMemory", () => {
  let memory: EpisodicMemory;
  let provider: ModelProvider;

  beforeEach(async () => {
    await cleanWorkspace();
    provider = makeMockProvider();
    memory = new EpisodicMemory(testSpace, provider);
  });

  describe("load() and save()", () => {
    it("loads from empty file without error", async () => {
      await memory.load();
      expect(memory.getStats().total).toBe(0);
    });

    it("loads and restores episodes from file", async () => {
      const ep: Episode = {
        id: "ep_test_1",
        sessionId: "session_1",
        owlName: "TestOwl",
        date: Date.now(),
        summary: "Test episode",
        keyFacts: ["fact1"],
        topics: ["testing"],
        sentiment: "positive",
        userMessageCount: 3,
      };

      memory = new EpisodicMemory(testSpace, provider);
      await memory.load();
      // Directly add to internal map for testing
      (memory as any).episodes.set(ep.id, ep);
      await (memory as any).save();

      const memory2 = new EpisodicMemory(testSpace, provider);
      await memory2.load();
      expect(memory2.getStats().total).toBe(1);
    });
  });

  describe("extractFromSession()", () => {
    it("returns null for session with fewer than 2 messages", async () => {
      const session = makeSession({
        messages: [makeMessage("user", "Hello")],
      });

      const result = await memory.extractFromSession(session, provider);
      expect(result).toBeNull();
    });

    it("returns null for session with no user messages", async () => {
      const session = makeSession({
        messages: [makeMessage("assistant", "Hello")],
      });

      const result = await memory.extractFromSession(session, provider);
      expect(result).toBeNull();
    });

    it("extracts episode from valid session", async () => {
      const session = makeSession({
        messages: [
          makeMessage("user", "I need help with testing"),
          makeMessage("assistant", "Sure, what would you like to test?"),
          makeMessage("user", "I want to test vitest"),
        ],
      });

      const result = await memory.extractFromSession(session, provider);

      expect(result).not.toBeNull();
      expect(result!.summary).toBe("User asked about testing");
      expect(result!.keyFacts).toContain("User prefers vitest");
      expect(result!.topics).toContain("testing");
      expect(result!.sentiment).toBe("positive");
      expect(result!.sessionId).toBe("session_test_1");
      expect(result!.owlName).toBe("TestOwl");
      expect(result!.embedding).toBeDefined();
    });
  });

  describe("search()", () => {
    it("returns empty array when no episodes exist", async () => {
      await memory.load();
      const results = await memory.search("test");
      expect(results).toHaveLength(0);
    });

    it("filters by keyword match in summary, topics, and keyFacts", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "User booked a meeting room",
        keyFacts: ["Meeting scheduled for 3pm"],
        topics: ["meeting", "scheduling"],
        userMessageCount: 2,
      });

      const results = await memory.search("meeting");
      expect(results).toHaveLength(1);
      expect(results[0].summary).toContain("meeting");
    });

    it("respects limit parameter", async () => {
      await memory.load();
      for (let i = 0; i < 5; i++) {
        (memory as any).episodes.set(`ep${i}`, {
          id: `ep${i}`,
          sessionId: `s${i}`,
          owlName: "Owl",
          date: Date.now(),
          summary: `Topic ${i}`,
          keyFacts: [],
          topics: ["test"],
          userMessageCount: 1,
        });
      }

      const results = await memory.search("Topic", 3);
      expect(results).toHaveLength(3);
    });
  });

  describe("getRecent()", () => {
    it("returns episodes sorted by date descending", async () => {
      await memory.load();
      const now = Date.now();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: now - 86400000,
        summary: "Older episode",
        keyFacts: [],
        topics: [],
        userMessageCount: 1,
      });
      (memory as any).episodes.set("ep2", {
        id: "ep2",
        sessionId: "s2",
        owlName: "Owl",
        date: now,
        summary: "Newer episode",
        keyFacts: [],
        topics: [],
        userMessageCount: 1,
      });

      const results = memory.getRecent(10);
      expect(results[0].summary).toBe("Newer episode");
      expect(results[1].summary).toBe("Older episode");
    });

    it("respects limit", async () => {
      await memory.load();
      for (let i = 0; i < 5; i++) {
        (memory as any).episodes.set(`ep${i}`, {
          id: `ep${i}`,
          sessionId: `s${i}`,
          owlName: "Owl",
          date: Date.now() - i * 1000,
          summary: `Episode ${i}`,
          keyFacts: [],
          topics: [],
          userMessageCount: 1,
        });
      }

      const results = memory.getRecent(2);
      expect(results).toHaveLength(2);
    });
  });

  describe("getByTopic()", () => {
    it("filters episodes by topic (case-insensitive)", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Meeting discussion",
        keyFacts: [],
        topics: ["meeting", "scheduling"],
        userMessageCount: 1,
      });

      const results = memory.getByTopic("MEETING");
      expect(results).toHaveLength(1);
      expect(results[0].topics).toContain("meeting");
    });
  });

  describe("toContextString()", () => {
    it("returns empty string when no episodes", async () => {
      await memory.load();
      const context = await memory.toContextString("test");
      expect(context).toBe("");
    });

    it("formats episodes as context string", async () => {
      await memory.load();
      const now = Date.now();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: now,
        summary: "User discussed testing",
        keyFacts: ["fact A", "fact B"],
        topics: ["testing"],
        userMessageCount: 2,
      });

      const context = await memory.toContextString("test", 1);
      expect(context).toContain("<episodic_memory>");
      expect(context).toContain("User discussed testing");
      expect(context).toContain("fact A");
    });
  });

  describe("getStats()", () => {
    it("returns correct topic counts", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Episode 1",
        keyFacts: [],
        topics: ["testing", "testing"], // duplicate
        userMessageCount: 1,
      });

      const stats = memory.getStats();
      expect(stats.total).toBe(1);
      expect(stats.topics["testing"]).toBe(2);
    });
  });

  describe("searchWithScoring()", () => {
    it("returns empty array when no episodes", async () => {
      await memory.load();
      const results = await memory.searchWithScoring("test");
      expect(results).toHaveLength(0);
    });

    it("filters out archived episodes", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Archived episode",
        keyFacts: [],
        topics: ["archived"],
        userMessageCount: 1,
        archived: true,
      });

      const results = await memory.searchWithScoring("archived");
      expect(results).toHaveLength(0);
    });

    it("applies threshold filtering", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Low relevance",
        keyFacts: [],
        topics: [],
        userMessageCount: 1,
        importance: 0.1,
      });

      const results = await memory.searchWithScoring(
        "low relevance nothing matches",
        5,
        undefined,
        0.5,
      );
      expect(results.every((r) => r.retrievalScore >= 0.5)).toBe(true);
    });
  });

  describe("runDecay()", () => {
    it("compresses old high-importance episodes", async () => {
      await memory.load();
      const now = Date.now();
      const DAY_MS = 24 * 60 * 60 * 1000;

      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: now - 35 * DAY_MS,
        summary: "Old episode",
        keyFacts: ["important fact"],
        topics: ["old"],
        userMessageCount: 1,
        importance: 0.4,
        embedding: [0.1, 0.2],
      });

      const result = memory.runDecay();

      expect(result.compressed).toBe(1);
      const ep = (memory as any).episodes.get("ep1");
      expect(ep.compressed).toBe(true);
      expect(ep.keyFacts).toHaveLength(0);
      expect(ep.embedding).toBeUndefined();
    });

    it("archives very old low-importance episodes", async () => {
      await memory.load();
      const now = Date.now();
      const DAY_MS = 24 * 60 * 60 * 1000;

      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: now - 95 * DAY_MS,
        summary: "Very old episode",
        keyFacts: ["some fact"],
        topics: ["old"],
        userMessageCount: 1,
        importance: 0.2,
      });

      const result = memory.runDecay();

      expect(result.archived).toBe(1);
      const ep = (memory as any).episodes.get("ep1");
      expect(ep.archived).toBe(true);
    });

    it("does nothing to recent episodes", async () => {
      await memory.load();
      (memory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Recent episode",
        keyFacts: ["fact"],
        topics: ["recent"],
        userMessageCount: 1,
        importance: 0.5,
      });

      const result = memory.runDecay();

      expect(result.compressed).toBe(0);
      expect(result.archived).toBe(0);
    });
  });

  describe("cosineSimilarity()", () => {
    it("returns 1 for identical vectors", () => {
      const sim = (memory as any).cosineSimilarity([1, 0], [1, 0]);
      expect(sim).toBeCloseTo(1);
    });

    it("returns 0 for orthogonal vectors", () => {
      const sim = (memory as any).cosineSimilarity([1, 0], [0, 1]);
      expect(sim).toBeCloseTo(0);
    });

    it("handles zero vectors", () => {
      const sim = (memory as any).cosineSimilarity([0, 0], [1, 1]);
      expect(sim).toBe(0);
    });
  });
});

describe("FactStore", () => {
  let store: FactStore;

  beforeEach(async () => {
    await cleanWorkspace();
    store = new FactStore(testSpace);
  });

  describe("load() and save()", () => {
    it("loads from empty file without error", async () => {
      await store.load();
      expect(store.getStats().total).toBe(0);
    });

    it("persists facts across store instances", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "User prefers dark mode",
        category: "preference",
        confidence: 0.9,
        source: "explicit",
      });

      const store2 = new FactStore(testSpace);
      await store2.load();
      const facts = store2.getForUser("user1");

      expect(facts).toHaveLength(1);
      expect(facts[0].fact).toBe("User prefers dark mode");
    });
  });

  describe("add()", () => {
    it("adds a new fact with generated id and timestamps", async () => {
      await store.load();
      const fact = await store.add({
        userId: "user1",
        fact: "Test fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      expect(fact.id).toMatch(/^fact_/);
      expect(fact.createdAt).toBe(fact.updatedAt);
      expect(fact.accessCount).toBe(0);
    });

    it("increments accessCount on retrieval", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Test fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      store.search("test");
      const retrieved = store.getAll()[0];
      expect(retrieved.accessCount).toBe(1);
    });
  });

  describe("addBatch()", () => {
    it("adds multiple facts and returns all stored facts", async () => {
      await store.load();
      const results = await store.addBatch([
        {
          userId: "user1",
          fact: "Fact 1",
          category: "preference",
          confidence: 0.8,
          source: "explicit",
        },
        {
          userId: "user1",
          fact: "Fact 2",
          category: "personal",
          confidence: 0.7,
          source: "inferred",
        },
      ]);

      expect(results).toHaveLength(2);
      expect(store.getStats().total).toBe(2);
    });
  });

  describe("update()", () => {
    it("updates an existing fact", async () => {
      await store.load();
      const added = await store.add({
        userId: "user1",
        fact: "Original fact",
        category: "preference",
        confidence: 0.5,
        source: "inferred",
      });

      const updated = await store.update(added.id, {
        fact: "Updated fact",
        confidence: 0.9,
      });

      expect(updated).not.toBeNull();
      expect(updated!.fact).toBe("Updated fact");
      expect(updated!.confidence).toBe(0.9);
      expect(updated!.source).toBe("inferred"); // unchanged
    });

    it("returns null for non-existent fact", async () => {
      await store.load();
      const result = await store.update("nonexistent", { fact: "test" });
      expect(result).toBeNull();
    });
  });

  describe("retire()", () => {
    it("soft-deletes a fact by setting confidence to 0", async () => {
      await store.load();
      const fact = await store.add({
        userId: "user1",
        fact: "Fact to retire",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      const result = await store.retire(fact.id, "User corrected this");
      expect(result).toBe(true);

      const retired = store.get(fact.id);
      expect(retired!.confidence).toBe(0);
      expect(retired!.contradictedBy).toContain("User corrected this");
    });

    it("returns false for non-existent fact", async () => {
      await store.load();
      const result = await store.retire("nonexistent");
      expect(result).toBe(false);
    });
  });

  describe("confirm()", () => {
    it("boosts confidence and marks as confirmed", async () => {
      await store.load();
      const fact = await store.add({
        userId: "user1",
        fact: "Inferred fact",
        category: "preference",
        confidence: 0.6,
        source: "inferred",
      });

      const confirmed = await store.confirm(fact.id, "user1");
      expect(confirmed).not.toBeNull();
      expect(confirmed!.source).toBe("confirmed");
      expect(confirmed!.confidence).toBe(0.95);
      expect(confirmed!.confirmedBy).toBe("user1");
    });
  });

  describe("get() / getAll() / getForUser()", () => {
    it("retrieves facts by id", async () => {
      await store.load();
      const added = await store.add({
        userId: "user1",
        fact: "Test",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      const retrieved = store.get(added.id);
      expect(retrieved).not.toBeNull();
      expect(retrieved!.fact).toBe("Test");
    });

    it("returns all facts across users", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Fact 1",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });
      await store.add({
        userId: "user2",
        fact: "Fact 2",
        category: "personal",
        confidence: 0.7,
        source: "explicit",
      });

      expect(store.getAll()).toHaveLength(2);
    });

    it("filters facts by userId", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "User 1 fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });
      await store.add({
        userId: "user2",
        fact: "User 2 fact",
        category: "preference",
        confidence: 0.7,
        source: "explicit",
      });

      const user1Facts = store.getForUser("user1");
      expect(user1Facts).toHaveLength(1);
      expect(user1Facts[0].fact).toBe("User 1 fact");
    });
  });

  describe("getActiveForUser()", () => {
    it("filters out retired and expired facts", async () => {
      await store.load();
      const active = await store.add({
        userId: "user1",
        fact: "Active fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      await store.add({
        userId: "user1",
        fact: "Retired fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });
      await store.retire(
        (
          await store.add({
            userId: "user1",
            fact: "Will be retired",
            category: "preference",
            confidence: 0.8,
            source: "explicit",
          })
        ).id,
      );

      const activeFacts = store.getActiveForUser("user1");
      expect(activeFacts.every((f) => f.confidence > 0)).toBe(true);
    });
  });

  describe("search()", () => {
    it("matches fact content", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "User prefers TypeScript",
        category: "skill",
        confidence: 0.9,
        source: "explicit",
      });

      const results = store.search("TypeScript");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].fact).toContain("TypeScript");
    });

    it("boosts score for entity matches", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Project uses React",
        entity: "ProjectX",
        category: "project_detail",
        confidence: 0.9,
        source: "explicit",
      });

      const results = store.search("ProjectX");
      expect(results[0].entity).toBe("ProjectX");
    });

    it("respects limit", async () => {
      await store.load();
      for (let i = 0; i < 5; i++) {
        await store.add({
          userId: "user1",
          fact: `Fact ${i}`,
          category: "preference",
          confidence: 0.8,
          source: "explicit",
        });
      }

      const results = store.search("fact", "user1", 2);
      expect(results).toHaveLength(2);
    });
  });

  describe("getByCategory()", () => {
    it("filters by category", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Likes coffee",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });
      await store.add({
        userId: "user1",
        fact: "Working on API",
        category: "project_detail",
        confidence: 0.8,
        source: "explicit",
      });

      const prefs = store.getByCategory("user1", "preference");
      expect(prefs).toHaveLength(1);
      expect(prefs[0].category).toBe("preference");
    });
  });

  describe("getByEntity()", () => {
    it("finds facts mentioning an entity", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Uses Vim for editing",
        entity: "Vim",
        category: "skill",
        confidence: 0.8,
        source: "explicit",
      });

      const results = store.getByEntity("vim");
      expect(results).toHaveLength(1);
      expect(results[0].entity).toBe("Vim");
    });
  });

  describe("getRelated()", () => {
    it("finds facts with same entity or category", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Uses Vim",
        entity: "Vim",
        category: "skill",
        confidence: 0.8,
        source: "explicit",
      });
      const related = await store.add({
        userId: "user1",
        fact: "Vim has modal editing",
        entity: "Vim",
        category: "skill",
        confidence: 0.7,
        source: "inferred",
      });

      const results = store.getRelated(related.id);
      expect(results.some((f) => f.fact.includes("Uses Vim"))).toBe(true);
    });
  });

  describe("getStats()", () => {
    it("aggregates category, source, and confidence statistics", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Prefers coffee",
        category: "preference",
        confidence: 0.9,
        source: "explicit",
      });
      await store.add({
        userId: "user1",
        fact: "Likes tea",
        category: "preference",
        confidence: 0.7,
        source: "inferred",
      });

      const stats = store.getStats("user1");
      expect(stats.total).toBe(2);
      expect(stats.byCategory["preference"]).toBe(2);
      expect(stats.bySource["explicit"]).toBe(1);
      expect(stats.bySource["inferred"]).toBe(1);
      expect(stats.avgConfidence).toBeCloseTo(0.8);
    });
  });

  describe("purgeExpired()", () => {
    it("removes facts past their expiration date", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "Expired fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
        expiresAt: new Date(Date.now() - 86400000).toISOString(), // yesterday
      });

      await store.add({
        userId: "user1",
        fact: "Active fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
        expiresAt: new Date(Date.now() + 86400000).toISOString(), // tomorrow
      });

      const removed = await store.purgeExpired();
      expect(removed).toBe(1);
      expect(store.getStats("user1").total).toBe(1);
    });
  });

  describe("applyDefaultTtl()", () => {
    it("sets expiration on facts without one", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "No TTL fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      const updated = await store.applyDefaultTtl();
      expect(updated).toBe(1);

      const fact = store.getAll()[0];
      expect(fact.expiresAt).toBeDefined();
    });
  });

  describe("conflict resolution", () => {
    it("detects same fact (normalized)", async () => {
      await store.load();
      const store2 = new FactStore(testSpace);

      await store.add({
        userId: "user1",
        fact: "User prefers TypeScript",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      await store2.load();
      const added2 = await store2.add({
        userId: "user1",
        fact: "User prefers typescript", // same after normalization
        category: "preference",
        confidence: 0.9,
        source: "inferred",
      });

      // Should be resolved (updated existing) rather than creating duplicate
      expect(store2.getForUser("user1").length).toBe(1);
    });

    it("detects negated facts and retires old", async () => {
      await store.load();
      await store.add({
        userId: "user1",
        fact: "User likes coffee",
        category: "preference",
        confidence: 0.8,
        source: "inferred",
      });

      await store.add({
        userId: "user1",
        fact: "User does NOT like coffee",
        category: "preference",
        confidence: 0.9,
        source: "explicit",
      });

      const facts = store.getForUser("user1");
      expect(facts.length).toBe(2); // both stored, old marked as contradicted
      const retired = facts.find((f) => f.confidence === 0);
      expect(retired).toBeDefined();
    });
  });
});

describe("SessionSegmenter", () => {
  describe("findSegments()", () => {
    it("returns empty array for empty session", () => {
      const session = makeSession({ messages: [] });
      const segments = findSegments(session);
      expect(segments).toHaveLength(0);
    });

    it("returns single segment for session with no gaps", () => {
      const now = Date.now();
      const session = makeSession({
        messages: [
          makeMessage("user", "Hello", now),
          makeMessage("assistant", "Hi", now + 1000),
          makeMessage("user", "How are you?", now + 2000),
        ],
      });

      const segments = findSegments(session);
      expect(segments).toHaveLength(1);
      expect(segments[0].startIndex).toBe(0);
      expect(segments[0].endIndex).toBe(2);
      expect(segments[0].messageCount).toBe(3);
    });

    it("splits on 30+ minute gaps", () => {
      const now = Date.now();
      const GAP_MS = 31 * 60 * 1000; // 31 minutes
      const session = makeSession({
        messages: [
          makeMessage("user", "Hello", now),
          makeMessage("assistant", "Hi", now + 1000),
          makeMessage("user", "Still there?", now + GAP_MS),
          makeMessage("assistant", "Yes", now + GAP_MS + 1000),
        ],
      });

      const segments = findSegments(session);
      expect(segments).toHaveLength(2);
      expect(segments[0].messageCount).toBe(2);
      expect(segments[1].messageCount).toBe(2);
    });

    it("marks last segment as current (open)", () => {
      const now = Date.now();
      const session = makeSession({
        messages: [
          makeMessage("user", "Message 1", now),
          makeMessage("user", "Message 2", now + 3600000),
        ],
      });

      const segments = findSegments(session);
      expect(segments).toHaveLength(2); // 1 hour gap > 30 min threshold = split
      const lastSeg = segments[segments.length - 1];
      expect(lastSeg.startIndex).toBe(1);
      expect(lastSeg.messageCount).toBe(1);
    });
  });

  describe("getSegmentMessages()", () => {
    it("returns correct slice of messages", () => {
      const now = Date.now();
      const session = makeSession({
        messages: [
          makeMessage("user", "First"),
          makeMessage("assistant", "Second"),
          makeMessage("user", "Third"),
          makeMessage("assistant", "Fourth"),
        ],
      });

      const segment = {
        startIndex: 1,
        endIndex: 2,
        startedAt: now,
        endedAt: now,
        messageCount: 2,
      };

      const messages = getSegmentMessages(session, segment);
      expect(messages).toHaveLength(2);
      expect(messages[0].content).toBe("Second");
      expect(messages[1].content).toBe("Third");
    });
  });

  describe("getUnextractedSegments()", () => {
    it("returns empty when only one segment (current)", () => {
      const session = makeSession({
        messages: [makeMessage("user", "Hello")],
      });

      const segments = getUnextractedSegments(session, 0);
      expect(segments).toHaveLength(0);
    });

    it("returns completed segments after extracted index", () => {
      const now = Date.now();
      const GAP_MS = 31 * 60 * 1000;
      const session = makeSession({
        messages: [
          makeMessage("user", "Seg 0", now),
          makeMessage("user", "Seg 0 end", now + 1000),
          makeMessage("user", "Seg 1 start", now + GAP_MS),
          makeMessage("user", "Seg 1 end", now + GAP_MS + 1000),
          makeMessage("user", "Seg 2 start", now + GAP_MS * 2),
          makeMessage("user", "Seg 2 end", now + GAP_MS * 2 + 1000),
        ],
      });

      // 3 segments: seg0 (0-1), seg1 (2-3), seg2 (4-5 - current)
      // slice(0,-1) = [seg0, seg1], filter by startIndex >= 2
      // seg0: 0 >= 2 = false, seg1: 2 >= 2 = true
      // Result: [seg1] with startIndex=2
      const segments = getUnextractedSegments(session, 2);
      expect(segments).toHaveLength(1);
      expect(segments[0].startIndex).toBe(2);
    });

    it("returns empty when nothing qualifies", () => {
      const now = Date.now();
      const GAP_MS = 31 * 60 * 1000;
      const session = makeSession({
        messages: [
          makeMessage("user", "Seg 0", now),
          makeMessage("user", "Seg 0 end", now + 1000),
          makeMessage("user", "Seg 1 start", now + GAP_MS),
          makeMessage("user", "Seg 1 end", now + GAP_MS + 1000),
        ],
      });

      // 2 segments: seg0 (0-1), seg1 (2-3 - current)
      // slice(0,-1) = [seg0], filter by startIndex >= 3
      // seg0: 0 >= 3 = false
      // Result: []
      const segments = getUnextractedSegments(session, 3);
      expect(segments).toHaveLength(0);
    });

    it("returns empty when all segments extracted", () => {
      const now = Date.now();
      const GAP_MS = 31 * 60 * 1000;
      const session = makeSession({
        messages: [
          makeMessage("user", "Seg 1", now),
          makeMessage("user", "End seg 1", now + 1000),
          makeMessage("user", "Seg 2 start", now + GAP_MS),
          makeMessage("user", "Seg 2 end", now + GAP_MS + 1000),
        ],
      });

      // Extract beyond second segment
      const segments = getUnextractedSegments(session, 5);
      expect(segments).toHaveLength(0);
    });
  });
});

describe("MemoryRetriever", () => {
  let episodicMemory: EpisodicMemory;
  let factStore: FactStore;
  let knowledgeGraph: KnowledgeGraph;
  let preferenceModel: UserPreferenceModel;
  let pelletStore: PelletStore;
  let provider: ModelProvider;
  let retriever: MemoryRetriever;

  beforeEach(async () => {
    await cleanWorkspace();
    provider = makeMockProvider();
    episodicMemory = new EpisodicMemory(testSpace, provider);
    factStore = new FactStore(testSpace);
    pelletStore = {
      search: vi.fn().mockResolvedValue([]),
    } as unknown as PelletStore;
    knowledgeGraph = {
      semanticSearch: vi.fn().mockResolvedValue([]),
      search: vi.fn().mockResolvedValue([]),
    } as unknown as KnowledgeGraph;
    preferenceModel = {
      toContextString: vi.fn().mockReturnValue(""),
      getCommunicationStyle: vi.fn().mockReturnValue(""),
    } as unknown as UserPreferenceModel;

    retriever = new MemoryRetriever(
      episodicMemory,
      factStore,
      knowledgeGraph,
      preferenceModel,
      pelletStore,
      provider,
    );
  });

  describe("retrieve()", () => {
    it("queries all memory systems in parallel by default", async () => {
      await episodicMemory.load();
      (episodicMemory as any).episodes.set("ep1", {
        id: "ep1",
        sessionId: "s1",
        owlName: "Owl",
        date: Date.now(),
        summary: "Test episode",
        keyFacts: [],
        topics: ["test"],
        userMessageCount: 1,
        embedding: [0.1, 0.2],
      });

      await factStore.load();
      await factStore.add({
        userId: "user1",
        fact: "Test fact",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });

      const result = await retriever.retrieve({
        query: "test",
        userId: "user1",
      });

      expect(result.query).toBe("test");
      expect(result.retrievedAt).toBeDefined();
      expect(result.episodes.length).toBeGreaterThan(0);
      expect(result.facts.length).toBeGreaterThan(0);
    });

    it("skips episodes when includeEpisodes is false", async () => {
      const result = await retriever.retrieve({
        query: "test",
        includeEpisodes: false,
      });

      expect(result.episodes).toHaveLength(0);
    });

    it("skips facts when includeFacts is false", async () => {
      const result = await retriever.retrieve({
        query: "test",
        includeFacts: false,
      });

      expect(result.facts).toHaveLength(0);
    });

    it("handles partial failures gracefully", async () => {
      const brokenGraph = {
        semanticSearch: vi.fn().mockRejectedValue(new Error("Graph failed")),
        search: vi.fn().mockRejectedValue(new Error("Graph failed")),
      } as unknown as KnowledgeGraph;

      const brokenRetriever = new MemoryRetriever(
        episodicMemory,
        factStore,
        brokenGraph,
        preferenceModel,
        pelletStore,
        provider,
      );

      const result = await brokenRetriever.retrieve({ query: "test" });
      // Should not throw, just return empty for failed component
      expect(result.graphNodes).toHaveLength(0);
    });

    it("filters graph results by domain", async () => {
      (knowledgeGraph.semanticSearch as any).mockResolvedValue([
        {
          id: "n1",
          title: "Node 1",
          domain: "coding",
          content: "",
          source: "test",
          confidence: 0.9,
          createdAt: "",
          updatedAt: "",
          accessCount: 0,
        },
        {
          id: "n2",
          title: "Node 2",
          domain: "personal",
          content: "",
          source: "test",
          confidence: 0.8,
          createdAt: "",
          updatedAt: "",
          accessCount: 0,
        },
      ]);

      const result = await retriever.retrieve({
        query: "test",
        domains: ["coding"],
      });

      expect(result.graphNodes).toHaveLength(1);
      expect(result.graphNodes[0].domain).toBe("coding");
    });

    it("filters facts by category", async () => {
      await factStore.load();
      await factStore.add({
        userId: "user1",
        fact: "Prefers coffee",
        category: "preference",
        confidence: 0.8,
        source: "explicit",
      });
      await factStore.add({
        userId: "user1",
        fact: "Working on API",
        category: "project_detail",
        confidence: 0.8,
        source: "explicit",
      });

      const result = await retriever.retrieve({
        query: "test",
        userId: "user1",
        categories: ["preference"],
      });

      expect(result.facts.every((f) => f.category === "preference")).toBe(true);
    });
  });

  describe("toContextString()", () => {
    it("returns empty string when no results", async () => {
      const result = await retriever.toContextString({
        episodes: [],
        facts: [],
        graphNodes: [],
        preferences: null,
        pellets: [],
        query: "test",
        retrievedAt: new Date().toISOString(),
      });

      expect(result).toBe("");
    });

    it("formats episodes as episodic_memory section", async () => {
      const result = await retriever.toContextString({
        episodes: [
          {
            id: "ep1",
            sessionId: "s1",
            owlName: "Owl",
            date: new Date("2024-01-15").getTime(),
            summary: "Discussed testing",
            keyFacts: ["Uses vitest"],
            topics: ["testing"],
            userMessageCount: 3,
          },
        ],
        facts: [],
        graphNodes: [],
        preferences: null,
        pellets: [],
        query: "test",
        retrievedAt: new Date().toISOString(),
      });

      expect(result).toContain("<episodic_memory>");
      expect(result).toContain("Discussed testing");
      expect(result).toContain("Uses vitest");
    });

    it("formats facts with confidence indicators", async () => {
      const result = await retriever.toContextString({
        episodes: [],
        facts: [
          {
            id: "f1",
            userId: "user1",
            fact: "Prefers dark mode",
            entity: "Editor",
            category: "preference",
            confidence: 0.9,
            source: "explicit",
            createdAt: "",
            updatedAt: "",
            accessCount: 0,
          },
          {
            id: "f2",
            userId: "user1",
            fact: "Likes short responses",
            category: "preference",
            confidence: 0.4,
            source: "inferred",
            createdAt: "",
            updatedAt: "",
            accessCount: 0,
          },
        ],
        graphNodes: [],
        preferences: null,
        pellets: [],
        query: "test",
        retrievedAt: new Date().toISOString(),
      });

      expect(result).toContain("<user_facts>");
      expect(result).toContain("✓"); // high confidence
      expect(result).toContain("~"); // low confidence
    });

    it("formats graph nodes with domain prefix", async () => {
      const result = await retriever.toContextString({
        episodes: [],
        facts: [],
        graphNodes: [
          {
            id: "n1",
            title: "Vim Config",
            domain: "coding",
            content: "My vim configuration for efficient editing",
            source: "test",
            confidence: 0.9,
            createdAt: "",
            updatedAt: "",
            accessCount: 0,
          },
        ],
        preferences: null,
        pellets: [],
        query: "test",
        retrievedAt: new Date().toISOString(),
      });

      expect(result).toContain("<knowledge_graph>");
      expect(result).toContain("[coding]");
      expect(result).toContain("Vim Config");
    });

    it("includes pellets section when present", async () => {
      const result = await retriever.toContextString({
        episodes: [],
        facts: [],
        graphNodes: [],
        preferences: null,
        pellets: [
          {
            id: "p1",
            title: "Testing Best Practices",
            content: "Use vitest for fast unit tests...",
            generatedAt: "",
            source: "parliament",
            owls: ["Noctua"],
            tags: ["testing"],
            version: 1,
          },
        ],
        query: "test",
        retrievedAt: new Date().toISOString(),
      });

      expect(result).toContain("<knowledge_pellets>");
      expect(result).toContain("Testing Best Practices");
    });

    it("respects maxEpisodes, maxFacts, maxPellets options", async () => {
      const result = await retriever.toContextString(
        {
          episodes: Array(5).fill({
            id: "ep",
            sessionId: "s",
            owlName: "Owl",
            date: Date.now(),
            summary: "Episode",
            keyFacts: [],
            topics: [],
            userMessageCount: 1,
          }),
          facts: Array(10).fill({
            id: "f",
            userId: "user1",
            fact: "Fact",
            category: "preference",
            confidence: 0.8,
            source: "explicit",
            createdAt: "",
            updatedAt: "",
            accessCount: 0,
          }),
          graphNodes: [],
          preferences: null,
          pellets: Array(5).fill({
            id: "p",
            title: "Pellet",
            content: "Content",
            generatedAt: "",
            source: "test",
            owls: [],
            tags: [],
            version: 1,
          }),
          query: "test",
          retrievedAt: new Date().toISOString(),
        },
        { maxEpisodes: 2, maxFacts: 3, maxPellets: 1 },
      );

      expect(result).toContain("Episode"); // but limited
      expect(result.split("✓").length - 1).toBeLessThanOrEqual(4); // facts limited
    });
  });
});
