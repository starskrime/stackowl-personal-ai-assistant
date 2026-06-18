import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { OutcomeJournal } from "../src/engine/outcome-journal.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase, journal: OutcomeJournal;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "owl-j-")); db = new MemoryDatabase(dir); journal = new OutcomeJournal(db); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("OutcomeJournal", () => {
  it("records and retrieves a run", async () => {
    const id = await journal.record({ sessionId: "s1", owlName: "atlas", userId: "u1", userMessage: "test", totalTurns: 3, toolsUsed: [], outcome: "success", reward: 0.8, qualityScore: 0.85, qualityFlags: [], taskCategory: "general", taskComplexity: "medium", degradationTier: 1, recoveryActions: [] });
    expect(id).toBeTruthy();
    const entries = await journal.getRecent(5);
    expect(entries[0].qualityScore).toBe(0.85);
  });
  it("updates follow-up sentiment", async () => {
    const id = await journal.record({ sessionId: "s1", owlName: "atlas", userId: "u1", userMessage: "test", totalTurns: 1, toolsUsed: [], outcome: "success", reward: 0.5, qualityScore: 0.7, qualityFlags: [], taskCategory: "general", taskComplexity: "simple", degradationTier: 1, recoveryActions: [] });
    await journal.updateSentiment(id, "correction");
    const entries = await journal.getRecent(1);
    expect(entries[0].followUpSentiment).toBe("correction");
  });
  it("getFailures returns low-quality entries", async () => {
    await journal.record({ sessionId: "s1", owlName: "atlas", userId: "u1", userMessage: "fail", totalTurns: 3, toolsUsed: [], outcome: "failure", reward: -0.5, qualityScore: 0.2, qualityFlags: [], taskCategory: "research", taskComplexity: "complex", degradationTier: 3, recoveryActions: [] });
    const fails = await journal.getFailures({ minEntries: 1 });
    expect(fails.length).toBe(1);
    expect(fails[0].qualityScore).toBeLessThan(0.5);
  });
});
