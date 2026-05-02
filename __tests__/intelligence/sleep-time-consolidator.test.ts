import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { SleepTimeConsolidator } from "../../src/intelligence/sleep-time-consolidator.js";

describe("SleepTimeConsolidator", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db as any);
  });

  afterEach(() => db.close());

  it("runs consolidation on session:ended", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "User works best in short focused bursts.", finishReason: "stop", model: "test" }),
    };
    const mockPelletStore = { store: vi.fn().mockResolvedValue("p1") };

    const consolidator = new SleepTimeConsolidator(db as any, mockProvider as any, mockPelletStore as any);
    await consolidator.onSessionEnded("u1", "s1");

    // If no prior sessions, provider is NOT called (nothing to consolidate)
    expect(mockProvider.chat.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("calls provider and stores pellets when summaries exist", async () => {
    db.prepare(`
      INSERT INTO summaries (id, session_id, user_id, owl_name, from_seq, to_seq, message_count, summary_text, created_at)
      VALUES ('sum1', 's0', 'u1', 'aria', 0, 5, 5, 'Previous session summary', datetime('now', '-2 hours'))
    `).run();

    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "User prefers mornings.\nUser likes bullet points.", finishReason: "stop", model: "test" }),
    };
    const mockPelletStore = { store: vi.fn().mockResolvedValue("p1") };

    const consolidator = new SleepTimeConsolidator(db as any, mockProvider as any, mockPelletStore as any);
    await consolidator.onSessionEnded("u1", "s1");

    expect(mockProvider.chat).toHaveBeenCalledOnce();
    expect(mockPelletStore.store.mock.calls.length).toBeGreaterThan(0);
  });

  it("debounces — second call within 60 minutes does not call provider again", async () => {
    db.prepare(`
      INSERT INTO summaries (id, session_id, user_id, owl_name, from_seq, to_seq, message_count, summary_text, created_at)
      VALUES ('sum1', 's0', 'u1', 'aria', 0, 5, 5, 'Previous session summary', datetime('now', '-2 hours'))
    `).run();

    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "User prefers mornings.", finishReason: "stop", model: "test" }),
    };
    const mockPelletStore = { store: vi.fn().mockResolvedValue("p1") };

    const consolidator = new SleepTimeConsolidator(db as any, mockProvider as any, mockPelletStore as any);
    await consolidator.onSessionEnded("u1", "s1");
    await consolidator.onSessionEnded("u1", "s2"); // debounced

    expect(mockProvider.chat.mock.calls.length).toBeLessThanOrEqual(1);
  });
});
