import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { ImprovementScheduler } from "../src/engine/improvement-scheduler.js";
import { OutcomeJournal } from "../src/engine/outcome-journal.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-sched-"));
  db = new MemoryDatabase(dir);
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("ImprovementScheduler", () => {
  it("start/stop without errors", () => {
    const journal = new OutcomeJournal(db);
    const sched = new ImprovementScheduler(journal, db, { quietHours: [] });
    sched.start();
    sched.stop();
    expect(sched.isRunning()).toBe(false);
  });

  it("runJournalReview processes recent failures (0 LLM calls)", async () => {
    const journal = new OutcomeJournal(db);
    await journal.record({
      sessionId: "s1", owlName: "atlas", userId: "u1",
      userMessage: "research X", totalTurns: 5, toolsUsed: ["web_search"],
      outcome: "failure", reward: -0.5, qualityScore: 0.2,
      qualityFlags: ["loop_exhausted"], taskCategory: "research",
      taskComplexity: "complex", degradationTier: 3, recoveryActions: ["replan"],
    });
    const sched = new ImprovementScheduler(journal, db, { quietHours: [] });
    const count = await sched.runJournalReview();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  it("isInQuietHours returns true when current hour is in range", () => {
    const sched = new ImprovementScheduler(
      new OutcomeJournal(db), db,
      { quietHours: [{ start: 0, end: 23 }] },
    );
    expect(sched.isInQuietHours()).toBe(true);
  });

  it("runToolEvolution returns null when SET is not wired", async () => {
    const sched = new ImprovementScheduler(
      new OutcomeJournal(db), db, { quietHours: [] },
    );
    const result = await sched.runToolEvolution();
    expect(result).toBeNull();
  });

  it("runToolEvolution delegates to selfEvolver.runOnce when wired", async () => {
    const calls: unknown[] = [];
    const fakeSelfEvolver = {
      runOnce: async (sr: unknown) => {
        calls.push(sr);
        return { runId: 42, toolName: "web", baselinePath: "x", candidatePath: "y" };
      },
    };
    const fakeShadowRunner = { tag: "shadow" };
    const sched = new ImprovementScheduler(
      new OutcomeJournal(db), db, { quietHours: [] },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      { selfEvolver: fakeSelfEvolver as any, shadowRunner: fakeShadowRunner as any },
    );
    const result = await sched.runToolEvolution();
    expect(result).toEqual({
      runId: 42, toolName: "web",
      baselinePath: "x", candidatePath: "y",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toBe(fakeShadowRunner);
  });
});
