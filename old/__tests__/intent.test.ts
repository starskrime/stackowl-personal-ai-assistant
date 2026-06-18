import { describe, it, expect, beforeEach, vi } from "vitest";
import { IntentStateMachine } from "../src/intent/state-machine.js";
import { CommitmentTrackerImpl } from "../src/intent/commitment-tracker.js";
import type { Intent, IntentType } from "../src/intent/types.js";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import os from "node:os";

vi.mock("node:fs/promises");
vi.mock("node:fs");

describe("IntentStateMachine", () => {
  let sm: IntentStateMachine;
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await os.tmpdir();
    sm = new IntentStateMachine(tmpDir);

    vi.resetAllMocks();
    vi.mocked(existsSync).mockReturnValue(false);
    vi.mocked(mkdir).mockResolvedValue(undefined as unknown as never);
    vi.mocked(writeFile).mockResolvedValue(undefined as unknown as never);
    vi.mocked(readFile).mockResolvedValue("[]");
  });

  describe("create", () => {
    it("should create an intent with pending status", () => {
      const intent = sm.create({
        rawQuery: "book a flight",
        description: "Book a flight to NYC",
        type: "task",
        sessionId: "session_1",
      });

      expect(intent.id).toMatch(/^intent_/);
      expect(intent.status).toBe("pending");
      expect(intent.description).toBe("Book a flight to NYC");
      expect(intent.type).toBe("task");
      expect(intent.sessionId).toBe("session_1");
      expect(intent.checkpoints).toEqual([]);
      expect(intent.commitments).toEqual([]);
    });

    it("should store intent in memory", () => {
      const intent = sm.create({
        rawQuery: "test query",
        description: "Test intent",
        type: "question",
        sessionId: "session_1",
      });

      const retrieved = sm.getBySession("session_1");
      expect(retrieved).toContain(intent);
    });

    it("should set timestamps", () => {
      const before = Date.now();
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });
      const after = Date.now();

      expect(intent.createdAt).toBeGreaterThanOrEqual(before);
      expect(intent.createdAt).toBeLessThanOrEqual(after);
      expect(intent.updatedAt).toBeGreaterThanOrEqual(before);
      expect(intent.updatedAt).toBeLessThanOrEqual(after);
      expect(intent.lastActiveAt).toBeGreaterThanOrEqual(before);
      expect(intent.lastActiveAt).toBeLessThanOrEqual(after);
    });
  });

  describe("transition", () => {
    it("should transition intent status", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "in_progress");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("in_progress");
    });

    it("should set blockedReason when transitioning to blocked", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "blocked", "API unavailable");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("blocked");
      expect(retrieved.blockedReason).toBe("API unavailable");
    });

    it("should update timestamps on transition", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });
      const originalUpdatedAt = intent.updatedAt;

      vi.useFakeTimers();
      vi.setSystemTime(Date.now() + 10000);

      sm.transition(intent.id, "in_progress");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.updatedAt).toBeGreaterThan(originalUpdatedAt);
      expect(retrieved.lastActiveAt).toBeGreaterThan(originalUpdatedAt);

      vi.useRealTimers();
    });

    it("should do nothing for non-existent intent", () => {
      expect(() => sm.transition("non_existent", "in_progress")).not.toThrow();
    });
  });

  describe("addCheckpoint", () => {
    it("should add a checkpoint to an intent", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const cp = sm.addCheckpoint(intent.id, "Step 1: Do something");

      expect(cp.id).toMatch(/^cp_/);
      expect(cp.description).toBe("Step 1: Do something");
      expect(cp.completedAt).toBeUndefined();

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.checkpoints).toHaveLength(1);
      expect(retrieved.checkpoints[0].description).toBe("Step 1: Do something");
    });

    it("should throw for non-existent intent", () => {
      expect(() => sm.addCheckpoint("non_existent", "test")).toThrow(
        "Intent non_existent not found",
      );
    });
  });

  describe("completeCheckpoint", () => {
    it("should mark checkpoint as completed", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const cp = sm.addCheckpoint(intent.id, "Step 1");
      sm.completeCheckpoint(intent.id, cp.id, "owl");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.checkpoints[0].completedAt).toBeDefined();
      expect(retrieved.checkpoints[0].completedBy).toBe("owl");
    });

    it("should auto-complete intent when all checkpoints done", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "in_progress");
      const cp1 = sm.addCheckpoint(intent.id, "Step 1");
      const cp2 = sm.addCheckpoint(intent.id, "Step 2");

      sm.completeCheckpoint(intent.id, cp1.id, "user");
      sm.completeCheckpoint(intent.id, cp2.id, "user");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("completed");
    });

    it("should not transition if not in_progress", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const cp = sm.addCheckpoint(intent.id, "Step 1");
      sm.completeCheckpoint(intent.id, cp.id, "user");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("pending");
    });
  });

  describe("addCommitment", () => {
    it("should add a commitment to an intent", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const commitment = sm.addCommitment(intent.id, {
        statement: "I'll remind you tomorrow",
        madeAt: Date.now(),
        deadline: Date.now() + 86400000,
        followUpMessage: "Reminder: you wanted to book a flight",
        triggerType: "deadline",
      });

      expect(commitment.id).toMatch(/^commit_/);
      expect(commitment.fulfilled).toBe(false);
      expect(commitment.statement).toBe("I'll remind you tomorrow");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.commitments).toHaveLength(1);
    });

    it("should throw for non-existent intent", () => {
      expect(() =>
        sm.addCommitment("non_existent", {
          statement: "test",
          madeAt: Date.now(),
          triggerType: "deadline",
          followUpMessage: "test",
        }),
      ).toThrow("Intent non_existent not found");
    });
  });

  describe("fulfillCommitment", () => {
    it("should mark commitment as fulfilled", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const commitment = sm.addCommitment(intent.id, {
        statement: "I'll do this",
        madeAt: Date.now(),
        triggerType: "deadline",
        followUpMessage: "follow up",
      });

      sm.fulfillCommitment(intent.id, commitment.id);

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.commitments[0].fulfilled).toBe(true);
      expect(retrieved.commitments[0].fulfilledAt).toBeDefined();
    });

    it("should do nothing for non-existent intent", () => {
      expect(() =>
        sm.fulfillCommitment("non_existent", "commit_123"),
      ).not.toThrow();
    });
  });

  describe("getActive", () => {
    it("should return only non-completed, non-abandoned intents", () => {
      const intent1 = sm.create({
        rawQuery: "test1",
        description: "Active intent",
        type: "task",
        sessionId: "session_1",
      });

      const intent2 = sm.create({
        rawQuery: "test2",
        description: "Completed intent",
        type: "task",
        sessionId: "session_2",
      });

      sm.transition(intent2.id, "completed");

      const intent3 = sm.create({
        rawQuery: "test3",
        description: "Abandoned intent",
        type: "task",
        sessionId: "session_3",
      });

      sm.transition(intent3.id, "abandoned");

      const active = sm.getActive();

      expect(active).toHaveLength(1);
      expect(active[0].id).toBe(intent1.id);
    });
  });

  describe("getStale", () => {
    it("should return intents inactive beyond threshold", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const intent = sm.create({
        rawQuery: "test",
        description: "Stale intent",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "in_progress");

      vi.setSystemTime(1000000000000 + 31 * 60 * 1000);

      const stale = sm.getStale(30 * 60 * 1000);
      expect(stale).toHaveLength(1);
      expect(stale[0].id).toBe(intent.id);

      vi.useRealTimers();
    });

    it("should exclude completed and abandoned intents", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const intent = sm.create({
        rawQuery: "test",
        description: "Completed stale intent",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "completed");

      const stale = sm.getStale(0);
      expect(stale).toHaveLength(0);

      vi.useRealTimers();
    });
  });

  describe("getPendingCommitments", () => {
    it("should return all unfulfilled commitments from active intents", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.addCommitment(intent.id, {
        statement: "Promise 1",
        madeAt: Date.now(),
        triggerType: "deadline",
        followUpMessage: "follow up",
      });

      sm.addCommitment(intent.id, {
        statement: "Promise 2",
        madeAt: Date.now(),
        triggerType: "time_delay",
        followUpMessage: "follow up",
      });

      const pending = sm.getPendingCommitments();

      expect(pending).toHaveLength(2);
      expect(pending[0].commitment.statement).toBe("Promise 1");
      expect(pending[1].commitment.statement).toBe("Promise 2");
    });

    it("should not include fulfilled commitments", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const commitment = sm.addCommitment(intent.id, {
        statement: "Will fulfill",
        madeAt: Date.now(),
        triggerType: "deadline",
        followUpMessage: "follow up",
      });

      sm.fulfillCommitment(intent.id, commitment.id);

      const pending = sm.getPendingCommitments();

      expect(pending).toHaveLength(0);
    });
  });

  describe("getBySession", () => {
    it("should return all intents for a session", () => {
      sm.create({
        rawQuery: "test1",
        description: "Intent 1",
        type: "task",
        sessionId: "session_1",
      });

      sm.create({
        rawQuery: "test2",
        description: "Intent 2",
        type: "question",
        sessionId: "session_1",
      });

      sm.create({
        rawQuery: "test3",
        description: "Intent 3",
        type: "information",
        sessionId: "session_2",
      });

      const session1Intents = sm.getBySession("session_1");
      const session2Intents = sm.getBySession("session_2");

      expect(session1Intents).toHaveLength(2);
      expect(session2Intents).toHaveLength(1);
    });
  });

  describe("getActiveForSession", () => {
    it("should return only active (non-completed) intent for session", () => {
      const intent1 = sm.create({
        rawQuery: "test1",
        description: "Active",
        type: "task",
        sessionId: "session_1",
      });

      const intent2 = sm.create({
        rawQuery: "test2",
        description: "Completed",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent2.id, "completed");

      const active = sm.getActiveForSession("session_1");

      expect(active).toBeDefined();
      expect(active!.id).toBe(intent1.id);
    });
  });

  describe("linkToGoal", () => {
    it("should link intent to a goal", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.linkToGoal(intent.id, "goal_123");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.linkedGoalId).toBe("goal_123");
    });

    it("should do nothing for non-existent intent", () => {
      expect(() => sm.linkToGoal("non_existent", "goal_123")).not.toThrow();
    });
  });

  describe("touch", () => {
    it("should update lastActiveAt and updatedAt", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      const originalLastActive = intent.lastActiveAt;
      const originalUpdated = intent.updatedAt;

      vi.useFakeTimers();
      vi.setSystemTime(Date.now() + 5000);

      sm.touch(intent.id);

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.lastActiveAt).toBeGreaterThan(originalLastActive);
      expect(retrieved.updatedAt).toBeGreaterThan(originalUpdated);

      vi.useRealTimers();
    });

    it("should do nothing for non-existent intent", () => {
      expect(() => sm.touch("non_existent")).not.toThrow();
    });
  });

  describe("promoteToThread", () => {
    it("should promote intent to thread", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test thread",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Ongoing project discussion");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.isThread).toBe(true);
      expect(retrieved.summary).toBe("Ongoing project discussion");
      expect(retrieved.sessions).toContain("session_1");
      expect(retrieved.resumeCount).toBe(0);
    });

    it("should not re-promote already promoted thread", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "First promotion");
      sm.promoteToThread(intent.id, "Second promotion");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.summary).toBe("First promotion");
    });

    it("should do nothing for non-existent intent", () => {
      expect(() =>
        sm.promoteToThread("non_existent", "test summary"),
      ).not.toThrow();
    });
  });

  describe("getActiveThreads", () => {
    it("should return only thread intents that are active", () => {
      const threadIntent = sm.create({
        rawQuery: "test",
        description: "Thread intent",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(threadIntent.id, "Thread summary");

      sm.create({
        rawQuery: "test",
        description: "Regular intent",
        type: "task",
        sessionId: "session_2",
      });

      const threads = sm.getActiveThreads();

      expect(threads).toHaveLength(1);
      expect(threads[0].id).toBe(threadIntent.id);
    });

    it("should exclude completed/abandoned threads", () => {
      const threadIntent = sm.create({
        rawQuery: "test",
        description: "Thread intent",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(threadIntent.id, "Thread summary");
      sm.transition(threadIntent.id, "completed");

      const threads = sm.getActiveThreads();

      expect(threads).toHaveLength(0);
    });
  });

  describe("getThreadForTopic", () => {
    it("should find thread by keyword overlap", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Planning a trip to Paris");

      const found = sm.getThreadForTopic("I want to visit Paris");

      expect(found).toBeDefined();
      expect(found!.id).toBe(intent.id);
    });

    it("should return null when no match above threshold", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "JavaScript programming");

      const found = sm.getThreadForTopic("baking cookies recipe", 0.3);

      expect(found).toBeNull();
    });

    it("should return null when no threads exist", () => {
      const found = sm.getThreadForTopic("any topic");

      expect(found).toBeNull();
    });
  });

  describe("resumeThread", () => {
    it("should increment resume count and add session", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");
      sm.resumeThread(intent.id, "session_2");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.resumeCount).toBe(1);
      expect(retrieved.sessions).toContain("session_2");
    });

    it("should not duplicate session", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");
      sm.resumeThread(intent.id, "session_1");
      sm.resumeThread(intent.id, "session_1");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.sessions).toHaveLength(1);
    });

    it("should reactivate abandoned/pending threads", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");
      sm.transition(intent.id, "abandoned");
      sm.resumeThread(intent.id, "session_2");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("in_progress");
    });

    it("should do nothing for non-thread intent", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      sm.resumeThread(intent.id, "session_2");

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.resumeCount).toBeUndefined();
    });
  });

  describe("decayThreads", () => {
    it("should abandon threads inactive for too long", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const intent = sm.create({
        rawQuery: "test",
        description: "Old thread",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");
      sm.transition(intent.id, "in_progress");

      vi.setSystemTime(1000000000000 + 15 * 24 * 60 * 60 * 1000);

      const decayed = sm.decayThreads(14);

      expect(decayed).toBe(1);

      const retrieved = sm.getBySession("session_1")[0];
      expect(retrieved.status).toBe("abandoned");

      vi.useRealTimers();
    });

    it("should not decay recently active threads", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const intent = sm.create({
        rawQuery: "test",
        description: "Recent thread",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");

      const decayed = sm.decayThreads(14);

      expect(decayed).toBe(0);

      vi.useRealTimers();
    });

    it("should not decay completed or abandoned threads", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const intent = sm.create({
        rawQuery: "test",
        description: "Completed thread",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Thread summary");
      sm.transition(intent.id, "completed");

      vi.setSystemTime(1000000000000 + 30 * 24 * 60 * 60 * 1000);

      const decayed = sm.decayThreads(14);

      expect(decayed).toBe(0);

      vi.useRealTimers();
    });
  });

  describe("toContextString", () => {
    it("should return empty string when no active intents", () => {
      const context = sm.toContextString();

      expect(context).toBe("");
    });

    it("should format active intents", () => {
      const intent = sm.create({
        rawQuery: "book flight",
        description: "Book a flight to NYC",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "in_progress");

      const context = sm.toContextString();

      expect(context).toContain("<active_intents>");
      expect(context).toContain("Book a flight to NYC");
      expect(context).toContain("🔄");
    });

    it("should include checkpoint progress", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Multi-step task",
        type: "task",
        sessionId: "session_1",
      });

      sm.addCheckpoint(intent.id, "Step 1");
      sm.addCheckpoint(intent.id, "Step 2");
      sm.completeCheckpoint(intent.id, intent.checkpoints[0].id, "user");

      const context = sm.toContextString();

      expect(context).toContain("✓○");
      expect(context).toContain("2 steps");
    });

    it("should include pending commitments count", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Task with commitments",
        type: "task",
        sessionId: "session_1",
      });

      sm.addCommitment(intent.id, {
        statement: "Promise 1",
        madeAt: Date.now(),
        triggerType: "deadline",
        followUpMessage: "follow up",
      });

      const context = sm.toContextString();

      expect(context).toContain("1 pending promise");
    });

    it("should include blocked reason", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Blocked task",
        type: "task",
        sessionId: "session_1",
      });

      sm.transition(intent.id, "blocked", "API rate limited");

      const context = sm.toContextString();

      expect(context).toContain("BLOCKED: API rate limited");
    });

    it("should include narrative threads section", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "Thread intent",
        type: "task",
        sessionId: "session_1",
      });

      sm.promoteToThread(intent.id, "Long-running project discussion");

      const context = sm.toContextString();

      expect(context).toContain("<narrative_threads>");
      expect(context).toContain("📌");
      expect(context).toContain("Long-running project discussion");
    });

    it("should truncate long context", () => {
      const intent = sm.create({
        rawQuery: "test",
        description: "A".repeat(1000),
        type: "task",
        sessionId: "session_1",
      });

      const context = sm.toContextString(100);

      expect(context).toContain("[truncated]");
      expect(context.length).toBeGreaterThan(100);
    });

    it("should limit to 5 intents and threads", () => {
      for (let i = 0; i < 10; i++) {
        const intent = sm.create({
          rawQuery: `test${i}`,
          description: `Intent ${i}`,
          type: "task",
          sessionId: `session_${i}`,
        });
        sm.promoteToThread(intent.id, `Thread ${i}`);
      }

      const context = sm.toContextString();

      const activeMatches =
        (context.match(/⏳/g) || []).length +
        (context.match(/🔄/g) || []).length +
        (context.match(/👆/g) || []).length +
        (context.match(/🚫/g) || []).length;
      const threadMatches = (context.match(/📌/g) || []).length;

      expect(activeMatches).toBeLessThanOrEqual(5);
      expect(threadMatches).toBeLessThanOrEqual(5);
    });
  });

  describe("load and save", () => {
    it("should load intents from file", async () => {
      const savedIntents: Intent[] = [
        {
          id: "intent_1",
          description: "Loaded intent",
          rawQuery: "test",
          type: "task" as IntentType,
          status: "in_progress",
          checkpoints: [],
          commitments: [],
          sessionId: "session_1",
          createdAt: Date.now(),
          updatedAt: Date.now(),
          lastActiveAt: Date.now(),
        },
      ];

      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFile).mockResolvedValue(JSON.stringify(savedIntents));

      await sm.load();

      const intents = sm.getBySession("session_1");
      expect(intents).toHaveLength(1);
      expect(intents[0].id).toBe("intent_1");
    });

    it("should not reload if already loaded", async () => {
      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFile).mockResolvedValue("[]");

      await sm.load();
      await sm.load();

      expect(readFile).toHaveBeenCalledTimes(1);
    });

    it("should call decayThreads on load", async () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      const decayedIntent: Intent = {
        id: "stale_thread",
        description: "Stale thread",
        rawQuery: "test",
        type: "task",
        status: "in_progress",
        checkpoints: [],
        commitments: [],
        sessionId: "session_1",
        createdAt: 1000000000000 - 20 * 24 * 60 * 60 * 1000,
        updatedAt: 1000000000000 - 20 * 24 * 60 * 60 * 1000,
        lastActiveAt: 1000000000000 - 20 * 24 * 60 * 60 * 1000,
        isThread: true,
        sessions: ["session_1"],
        resumeCount: 0,
      };

      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFile).mockResolvedValue(JSON.stringify([decayedIntent]));

      await sm.load();

      const intents = sm.getBySession("session_1");
      expect(intents[0].status).toBe("abandoned");

      vi.useRealTimers();
    });

    it("should save intents to file", async () => {
      sm.create({
        rawQuery: "test",
        description: "Test",
        type: "task",
        sessionId: "session_1",
      });

      await sm.save();

      expect(mkdir).toHaveBeenCalled();
      expect(writeFile).toHaveBeenCalledWith(
        expect.any(String),
        expect.any(String),
        "utf-8",
      );
    });
  });
});

describe("CommitmentTrackerImpl", () => {
  let tracker: CommitmentTrackerImpl;
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await os.tmpdir();
    tracker = new CommitmentTrackerImpl(tmpDir);

    vi.resetAllMocks();
    vi.mocked(existsSync).mockReturnValue(false);
    vi.mocked(mkdir).mockResolvedValue(undefined as unknown as never);
    vi.mocked(writeFile).mockResolvedValue(undefined as unknown as never);
    vi.mocked(readFile).mockResolvedValue("[]");
  });

  describe("track", () => {
    it("should create a tracked commitment", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "I'll remind you tomorrow",
        deadline: Date.now() + 86400000,
        followUpMessage: "Reminder: did you book the flight?",
        context: "User wants to book a flight",
        expiresAt: Date.now() + 172800000,
      });

      expect(tracked.id).toMatch(/^tracked_/);
      expect(tracked.status).toBe("pending");
      expect(tracked.statement).toBe("I'll remind you tomorrow");
      expect(tracked.intentId).toBe("intent_1");
    });

    it("should persist tracked commitment in memory", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      const pending = tracker.getPending();
      expect(pending).toHaveLength(1);
      expect(pending[0].id).toBe(tracked.id);
    });
  });

  describe("getDue", () => {
    it("should return commitments that are past deadline but not expired", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "Due commitment",
        deadline: 1000000000000 - 1000,
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.track({
        intentId: "intent_2",
        sessionId: "session_2",
        statement: "Future commitment",
        deadline: 1000000000000 + 100000,
        followUpMessage: "follow up",
        context: "context",
      });

      const due = tracker.getDue();

      expect(due).toHaveLength(1);
      expect(due[0].statement).toBe("Due commitment");

      vi.useRealTimers();
    });

    it("should exclude expired commitments from due", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "Expired commitment",
        deadline: 1000000000000 - 10000,
        followUpMessage: "follow up",
        context: "context",
        expiresAt: 1000000000000 - 1000,
      });

      const due = tracker.getDue();

      expect(due).toHaveLength(0);

      vi.useRealTimers();
    });
  });

  describe("getPending", () => {
    it("should return all pending commitments regardless of deadline", () => {
      tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "Pending 1",
        deadline: Date.now() + 100000,
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.track({
        intentId: "intent_2",
        sessionId: "session_2",
        statement: "Pending 2",
        deadline: Date.now() + 200000,
        followUpMessage: "follow up",
        context: "context",
      });

      const pending = tracker.getPending();

      expect(pending).toHaveLength(2);
    });

    it("should not include sent commitments", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "Will send",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markSent(tracked.id);

      const pending = tracker.getPending();

      expect(pending).toHaveLength(0);
    });
  });

  describe("markSent", () => {
    it("should mark commitment as sent", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markSent(tracked.id);

      expect(tracked.status).toBe("sent");
      expect(tracked.sentAt).toBeDefined();
    });

    it("should remove from pending after marking sent", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      expect(tracker.getPending()).toHaveLength(1);
      tracker.markSent(tracked.id);
      expect(tracker.getPending()).toHaveLength(0);
    });

    it("should do nothing for non-pending commitment", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markSent(tracked.id);

      const statusBefore = tracked.status;
      tracker.markSent(tracked.id);
      expect(tracked.status).toBe(statusBefore);
    });
  });

  describe("markAcknowledged", () => {
    it("should mark commitment as acknowledged", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markAcknowledged(tracked.id);

      expect(tracked.status).toBe("acknowledged");
      expect(tracked.acknowledgedAt).toBeDefined();
    });

    it("should do nothing for non-existent commitment", () => {
      expect(() => tracker.markAcknowledged("non_existent")).not.toThrow();
    });
  });

  describe("markDismissed", () => {
    it("should mark commitment as dismissed", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markDismissed(tracked.id);

      expect(tracked.status).toBe("dismissed");
      expect(tracked.dismissedAt).toBeDefined();
    });
  });

  describe("markExpired", () => {
    it("should mark commitment as expired", () => {
      const tracked = tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "test",
        deadline: Date.now(),
        followUpMessage: "follow up",
        context: "context",
      });

      tracker.markExpired(tracked.id);

      expect(tracked.status).toBe("expired");
    });
  });

  describe("toContextString", () => {
    it("should return empty string when no pending commitments", () => {
      const context = tracker.toContextString();

      expect(context).toBe("");
    });

    it("should format pending commitments", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "I'll book the flight tomorrow",
        deadline: 1000000000000 - 1000,
        followUpMessage: "Reminder",
        context: "context",
      });

      const context = tracker.toContextString();

      expect(context).toContain("<pending_commitments>");
      expect(context).toContain("🔔");
      expect(context).toContain("DUE NOW");
      expect(context).toContain("I'll book the flight tomorrow");
      expect(context).toContain("</pending_commitments>");

      vi.useRealTimers();
    });

    it("should show future deadline with clock icon", () => {
      vi.useFakeTimers();
      vi.setSystemTime(1000000000000);

      tracker.track({
        intentId: "intent_1",
        sessionId: "session_1",
        statement: "Future task",
        deadline: 1000000000000 + 86400000,
        followUpMessage: "follow up",
        context: "context",
      });

      const context = tracker.toContextString();

      expect(context).toContain("⏳");
      expect(context).toContain("due");

      vi.useRealTimers();
    });

    it("should limit to 5 commitments", () => {
      for (let i = 0; i < 10; i++) {
        tracker.track({
          intentId: `intent_${i}`,
          sessionId: `session_${i}`,
          statement: `Commitment ${i}`,
          deadline: Date.now() + 100000,
          followUpMessage: "follow up",
          context: "context",
        });
      }

      const context = tracker.toContextString();

      const lines = context.split("\n").filter((l) => l.includes("Commitment"));
      expect(lines.length).toBe(5);
    });
  });

  describe("load and save", () => {
    it("should load non-expired commitments from file", async () => {
      const savedCommitments = [
        {
          id: "tracked_1",
          intentId: "intent_1",
          sessionId: "session_1",
          statement: "Loaded commitment",
          deadline: Date.now(),
          followUpMessage: "follow up",
          context: "context",
          status: "pending" as const,
          createdAt: Date.now(),
        },
        {
          id: "tracked_expired",
          intentId: "intent_2",
          sessionId: "session_2",
          statement: "Expired commitment",
          deadline: Date.now(),
          followUpMessage: "follow up",
          context: "context",
          status: "expired" as const,
          createdAt: Date.now(),
        },
      ];

      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFile).mockResolvedValue(JSON.stringify(savedCommitments));

      await tracker.load();

      const pending = tracker.getPending();
      expect(pending).toHaveLength(1);
      expect(pending[0].statement).toBe("Loaded commitment");
    });

    it("should not reload if already loaded", async () => {
      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFile).mockResolvedValue("[]");

      await tracker.load();
      await tracker.load();

      expect(readFile).toHaveBeenCalledTimes(1);
    });
  });
});
