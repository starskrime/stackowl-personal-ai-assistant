import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CommitmentTrackerImpl } from "../src/intent/commitment-tracker.js";
import type { TrackedCommitment } from "../src/intent/commitment-tracker.js";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mkdir, writeFile, rm } from "node:fs/promises";

const TEST_DIR = join(tmpdir(), "commitment-tracker-test-" + Date.now());

async function setup() {
  await mkdir(join(TEST_DIR, "intents"), { recursive: true });
}

async function cleanup() {
  try {
    await rm(TEST_DIR, { recursive: true, force: true });
  } catch {}
}

function makeCommitment(
  overrides: Partial<
    Omit<TrackedCommitment, "id" | "status" | "createdAt">
  > = {},
): Omit<TrackedCommitment, "id" | "status" | "createdAt"> {
  const now = Date.now();
  return {
    intentId: "intent_123",
    sessionId: "session_456",
    statement: "Remember to check on the user tomorrow",
    deadline: now + 86400000,
    followUpMessage: "Hey, checking in as promised!",
    context: "User asked me to remind them",
    ...overrides,
  };
}

describe("CommitmentTracker", () => {
  let tracker: CommitmentTrackerImpl;

  beforeEach(async () => {
    await setup();
    tracker = new CommitmentTrackerImpl(TEST_DIR);
  });

  afterEach(async () => {
    await cleanup();
  });

  describe("track()", () => {
    it("creates a tracked commitment with generated id and pending status", () => {
      const input = makeCommitment();
      const result = tracker.track(input);

      expect(result.id).toMatch(/^tracked_\d+_[a-z0-9]+$/);
      expect(result.status).toBe("pending");
      expect(result.createdAt).toBeGreaterThan(0);
      expect(result.intentId).toBe(input.intentId);
      expect(result.sessionId).toBe(input.sessionId);
      expect(result.statement).toBe(input.statement);
      expect(result.deadline).toBe(input.deadline);
    });

    it("stores commitment in memory", () => {
      const input = makeCommitment();
      tracker.track(input);
      const pending = tracker.getPending();

      expect(pending.length).toBe(1);
      expect(pending[0].statement).toBe(input.statement);
    });

    it("generates unique ids for multiple commitments", () => {
      const c1 = tracker.track(makeCommitment());
      const c2 = tracker.track(makeCommitment());
      const c3 = tracker.track(makeCommitment());

      expect(c1.id).not.toBe(c2.id);
      expect(c2.id).not.toBe(c3.id);
    });
  });

  describe("getDue()", () => {
    it("returns commitments with deadline <= now and not expired", () => {
      const past = Date.now() - 1000;
      tracker.track(makeCommitment({ deadline: past }));

      const due = tracker.getDue();
      expect(due.length).toBe(1);
    });

    it("excludes commitments with future deadlines", () => {
      const future = Date.now() + 86400000;
      tracker.track(makeCommitment({ deadline: future }));

      const due = tracker.getDue();
      expect(due.length).toBe(0);
    });

    it("excludes commitments that have expiredAt before now", () => {
      const past = Date.now() - 2000;
      const expiredAt = Date.now() - 1000;
      tracker.track(makeCommitment({ deadline: past, expiresAt: expiredAt }));

      const due = tracker.getDue();
      expect(due.length).toBe(0);
    });

    it("includes commitments with expiresAt in the future", () => {
      const past = Date.now() - 1000;
      const futureExpiry = Date.now() + 10000;
      tracker.track(
        makeCommitment({ deadline: past, expiresAt: futureExpiry }),
      );

      const due = tracker.getDue();
      expect(due.length).toBe(1);
    });

    it("excludes non-pending commitments", () => {
      const past = Date.now() - 1000;
      const c = tracker.track(makeCommitment({ deadline: past }));
      tracker.markSent(c.id);

      expect(tracker.getDue().length).toBe(0);
    });
  });

  describe("getPending()", () => {
    it("returns all pending commitments regardless of deadline", () => {
      const past = Date.now() - 1000;
      const future = Date.now() + 86400000;
      tracker.track(makeCommitment({ deadline: past }));
      tracker.track(makeCommitment({ deadline: future }));

      const pending = tracker.getPending();
      expect(pending.length).toBe(2);
    });

    it("excludes sent commitments", () => {
      const c = tracker.track(makeCommitment());
      tracker.markSent(c.id);

      expect(tracker.getPending().length).toBe(0);
    });

    it("excludes acknowledged commitments", () => {
      const c = tracker.track(makeCommitment());
      tracker.markAcknowledged(c.id);

      expect(tracker.getPending().length).toBe(0);
    });

    it("excludes dismissed commitments", () => {
      const c = tracker.track(makeCommitment());
      tracker.markDismissed(c.id);

      expect(tracker.getPending().length).toBe(0);
    });

    it("excludes expired commitments", () => {
      const c = tracker.track(makeCommitment());
      tracker.markExpired(c.id);

      expect(tracker.getPending().length).toBe(0);
    });
  });

  describe("markSent()", () => {
    it("updates status to sent and sets sentAt", () => {
      const c = tracker.track(makeCommitment());
      tracker.markSent(c.id);

      expect(c.status).toBe("sent");
      expect(c.sentAt).toBeGreaterThan(0);
    });

    it("ignores unknown id", () => {
      expect(() => tracker.markSent("unknown_id")).not.toThrow();
    });

    it("ignores non-pending commitment", () => {
      const c = tracker.track(makeCommitment());
      tracker.markSent(c.id);
      tracker.markSent(c.id);

      expect(c.sentAt).toBeDefined();
    });
  });

  describe("markAcknowledged()", () => {
    it("updates status to acknowledged and sets acknowledgedAt", () => {
      const c = tracker.track(makeCommitment());
      tracker.markAcknowledged(c.id);

      expect(c.status).toBe("acknowledged");
      expect(c.acknowledgedAt).toBeGreaterThan(0);
    });

    it("ignores unknown id", () => {
      expect(() => tracker.markAcknowledged("unknown")).not.toThrow();
    });
  });

  describe("markDismissed()", () => {
    it("updates status to dismissed and sets dismissedAt", () => {
      const c = tracker.track(makeCommitment());
      tracker.markDismissed(c.id);

      expect(c.status).toBe("dismissed");
      expect(c.dismissedAt).toBeGreaterThan(0);
    });
  });

  describe("markExpired()", () => {
    it("updates status to expired", () => {
      const c = tracker.track(makeCommitment());
      tracker.markExpired(c.id);

      expect(c.status).toBe("expired");
    });
  });

  describe("toContextString()", () => {
    it("returns empty string when no pending commitments", () => {
      expect(tracker.toContextString()).toBe("");
    });

    it("formats pending commitments with correct structure", () => {
      const future = Date.now() + 86400000;
      tracker.track(
        makeCommitment({ statement: "Test commitment", deadline: future }),
      );

      const output = tracker.toContextString();

      expect(output).toContain("<pending_commitments>");
      expect(output).toContain("Test commitment");
      expect(output).toContain("</pending_commitments>");
    });

    it("shows due icon for overdue commitments", () => {
      const past = Date.now() - 1000;
      tracker.track(makeCommitment({ deadline: past }));

      const output = tracker.toContextString();
      expect(output).toContain("🔔");
      expect(output).toContain("DUE NOW");
    });

    it("shows waiting icon for future commitments", () => {
      const future = Date.now() + 86400000;
      tracker.track(makeCommitment({ deadline: future }));

      const output = tracker.toContextString();
      expect(output).toContain("⏳");
    });

    it("limits to 5 commitments", () => {
      for (let i = 0; i < 10; i++) {
        tracker.track(makeCommitment({ statement: `Commitment ${i}` }));
      }

      const output = tracker.toContextString();
      const matches = output.match(/Commitment \d+/g) ?? [];
      expect(matches.length).toBeLessThanOrEqual(5);
    });
  });

  describe("load/save", () => {
    it("persists commitments to disk", async () => {
      tracker.track(makeCommitment({ statement: "Persisted" }));
      await tracker.save();

      const newTracker = new CommitmentTrackerImpl(TEST_DIR);
      await newTracker.load();

      expect(newTracker.getPending().length).toBe(1);
      expect(newTracker.getPending()[0].statement).toBe("Persisted");
    });

    it("loads and filters out expired/dismissed commitments", async () => {
      const c = tracker.track(makeCommitment());
      tracker.markExpired(c.id);
      await tracker.save();

      const newTracker = new CommitmentTrackerImpl(TEST_DIR);
      await newTracker.load();

      expect(newTracker.getPending().length).toBe(0);
    });

    it("handles missing file gracefully on load", async () => {
      const emptyTracker = new CommitmentTrackerImpl("/nonexistent/path");
      await expect(emptyTracker.load()).resolves.not.toThrow();
    });
  });
});
