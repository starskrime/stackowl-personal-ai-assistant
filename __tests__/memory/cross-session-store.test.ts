import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { CrossSessionStore } from "../../src/memory/cross-session-store.js";
import type { Session } from "../../src/memory/store.js";
import type { ChatMessage } from "../../src/providers/base.js";
import type { FactStore } from "../../src/memory/fact-store.js";

const testSpace = join(__dirname, ".test_cross_session_workspace");

async function cleanWorkspace() {
  await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  await mkdir(testSpace, { recursive: true });
}

function makeMockFactStore(): FactStore {
  return {
    add: vi.fn().mockResolvedValue({
      id: "fact_1",
      userId: "default",
      fact: "Test fact",
      category: "preference",
      confidence: 0.8,
      source: "explicit",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      accessCount: 0,
    }),
  } as unknown as FactStore;
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
  role: "user" | "assistant",
  content: string,
): ChatMessage {
  return { role, content };
}

describe("CrossSessionStore", () => {
  let store: CrossSessionStore;

  beforeEach(async () => {
    await cleanWorkspace();
    store = new CrossSessionStore(testSpace);
  });

  afterEach(async () => {
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  });

  describe("load()", () => {
    it("creates empty data when file does not exist", async () => {
      await store.load();
      const stats = await store.getStats();

      expect(stats.totalCommitments).toBe(0);
      expect(stats.criticalFacts).toBe(0);
    });

    it("loads persisted data when file exists", async () => {
      await store.load();
      await store.addCommitment("Remember to fix the bug");
      await store.addCommitment("Complete the feature", "session-123");

      const store2 = new CrossSessionStore(testSpace);
      await store2.load();

      const commitments = await store2.getAllCommitments();
      expect(commitments.length).toBe(2);
    });
  });

  describe("save()", () => {
    it("persists data to disk", async () => {
      await store.load();
      await store.addCommitment("Test commitment");

      expect(existsSync(join(testSpace, "memory", "cross-session.json"))).toBe(true);
    });
  });

  describe("addCommitment()", () => {
    it("adds a commitment and returns it", async () => {
      await store.load();
      const commitment = await store.addCommitment("Remember to call me back");

      expect(commitment.id).toMatch(/^commit_/);
      expect(commitment.description).toBe("Remember to call me back");
      expect(commitment.status).toBe("pending");
      expect(commitment.createdAt).toBeDefined();
    });

    it("associates commitment with source session", async () => {
      await store.load();
      const commitment = await store.addCommitment("Fix the issue", "session-abc");

      expect(commitment.sourceSessionId).toBe("session-abc");
    });
  });

  describe("updateCommitmentStatus()", () => {
    it("updates commitment status", async () => {
      await store.load();
      const commitment = await store.addCommitment("Test commitment");

      const updated = await store.updateCommitmentStatus(commitment.id, "completed");
      expect(updated).toBe(true);

      const active = await store.getActiveCommitments();
      expect(active.some((c) => c.id === commitment.id)).toBe(false);
    });

    it("sets completedAt timestamp when completed", async () => {
      await store.load();
      const commitment = await store.addCommitment("Test commitment");

      await store.updateCommitmentStatus(commitment.id, "completed");

      const all = await store.getAllCommitments();
      const completed = all.find((c) => c.id === commitment.id);
      expect(completed!.completedAt).toBeDefined();
    });

    it("returns false for non-existent commitment", async () => {
      await store.load();
      const updated = await store.updateCommitmentStatus("nonexistent", "completed");
      expect(updated).toBe(false);
    });
  });

  describe("getActiveCommitments()", () => {
    it("returns only pending and in_progress commitments", async () => {
      await store.load();
      await store.addCommitment("Pending 1");
      await store.addCommitment("In Progress 2", "session-2");
      await store.addCommitment("Completed 3", "session-3");

      const all = await store.getAllCommitments();
      for (const c of all) {
        if (c.description.includes("Completed")) {
          await store.updateCommitmentStatus(c.id, "completed");
        } else if (c.description.includes("In Progress")) {
          await store.updateCommitmentStatus(c.id, "in_progress");
        }
      }

      const active = await store.getActiveCommitments();
      expect(active.length).toBe(2);
      expect(active.every((c) => c.status !== "completed")).toBe(true);
    });
  });

  describe("getAllCommitments()", () => {
    it("returns all commitments", async () => {
      await store.load();
      await store.addCommitment("Commitment 1");
      await store.addCommitment("Commitment 2");

      const all = await store.getAllCommitments();
      expect(all.length).toBe(2);
    });
  });

  describe("addCriticalFact()", () => {
    it("adds a critical fact with generated fields", async () => {
      await store.load();
      const fact = await store.addCriticalFact({
        userId: "user1",
        fact: "User prefers dark mode",
        category: "preference",
        confidence: 0.9,
        source: "explicit",
      });

      expect(fact.id).toMatch(/^cfact_/);
      expect(fact.createdAt).toBeDefined();
      expect(fact.accessCount).toBe(0);
    });
  });

  describe("getCriticalFacts()", () => {
    it("returns all critical facts", async () => {
      await store.load();
      await store.addCriticalFact({
        userId: "user1",
        fact: "Critical fact 1",
        category: "decision",
        confidence: 0.9,
        source: "explicit",
      });

      const facts = await store.getCriticalFacts();
      expect(facts.length).toBe(1);
    });
  });

  describe("buildContextString()", () => {
    it("returns empty string when no data", async () => {
      await store.load();
      const context = await store.buildContextString();
      expect(context).toBe("");
    });

    it("includes active commitments", async () => {
      await store.load();
      await store.addCommitment("Remember to fix the bug");
      await store.addCommitment("Complete the feature", "session-123");

      const context = await store.buildContextString();
      expect(context).toContain("Active Commitments");
      expect(context).toContain("Remember to fix the bug");
    });

    it("includes critical facts", async () => {
      await store.load();
      await store.addCriticalFact({
        userId: "user1",
        fact: "User prefers dark mode",
        category: "preference",
        confidence: 0.9,
        source: "explicit",
      });

      const context = await store.buildContextString();
      expect(context).toContain("Important Facts");
      expect(context).toContain("dark mode");
    });
  });

  describe("getStats()", () => {
    it("returns correct statistics", async () => {
      await store.load();
      await store.addCommitment("Pending 1");
      await store.addCommitment("Completed 2");

      const all = await store.getAllCommitments();
      await store.updateCommitmentStatus(all[1].id, "completed");

      const stats = await store.getStats();
      expect(stats.totalCommitments).toBe(2);
      expect(stats.activeCommitments).toBe(1);
      expect(stats.completedCommitments).toBe(1);
      expect(stats.criticalFacts).toBe(0);
    });
  });

  describe("extractFromSession()", () => {
    it("extracts commitments from session messages", async () => {
      const mockFactStore = makeMockFactStore();
      const storeWithFactStore = new CrossSessionStore(testSpace, mockFactStore);

      const session = makeSession({
        id: "session-with-commitment",
        messages: [
          makeMessage("user", "Please remember to call me back"),
          makeMessage("assistant", "Sure, I'll do that"),
        ],
      });

      await storeWithFactStore.load();
      await storeWithFactStore.extractFromSession(session);

      expect(mockFactStore.add).toHaveBeenCalled();
    });

    it("extracts decisions from session messages", async () => {
      const mockFactStore = makeMockFactStore();
      const storeWithFactStore = new CrossSessionStore(testSpace, mockFactStore);

      const session = makeSession({
        id: "session-with-decision",
        messages: [
          makeMessage("user", "We decided to use PostgreSQL for the database"),
          makeMessage("assistant", "Sounds good!"),
        ],
      });

      await storeWithFactStore.load();
      await storeWithFactStore.extractFromSession(session);

      expect(mockFactStore.add).toHaveBeenCalled();
    });
  });
});
