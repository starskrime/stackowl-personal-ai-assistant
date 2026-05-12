import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let activeCount = 0;
let peakConcurrent = 0;

function trackingFactory() {
  return {
    async run() {
      activeCount++;
      peakConcurrent = Math.max(peakConcurrent, activeCount);
      await new Promise((r) => setTimeout(r, 200));
      activeCount--;
      return { content: "done" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-conc-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  activeCount = 0;
  peakConcurrent = 0;
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionRunner concurrency cap", () => {
  it("respects maxConcurrent=2", async () => {
    const runner = new SessionRunner(store, trackingFactory, () => ({}), {
      maxConcurrent: 2,
    });
    await Promise.all([
      runner.spawn({ prompt: "a" }),
      runner.spawn({ prompt: "b" }),
      runner.spawn({ prompt: "c" }),
      runner.spawn({ prompt: "d" }),
      runner.spawn({ prompt: "e" }),
    ]);
    await new Promise((r) => setTimeout(r, 1500));
    expect(peakConcurrent).toBeLessThanOrEqual(2);
    expect(store.list({ status: "completed" })).toHaveLength(5);
    runner.stop();
  });

  it("default maxConcurrent=5 allows more concurrent tasks", async () => {
    const runner = new SessionRunner(store, trackingFactory, () => ({}));
    await Promise.all([
      runner.spawn({ prompt: "a" }),
      runner.spawn({ prompt: "b" }),
      runner.spawn({ prompt: "c" }),
      runner.spawn({ prompt: "d" }),
      runner.spawn({ prompt: "e" }),
    ]);
    await new Promise((r) => setTimeout(r, 500));
    expect(peakConcurrent).toBeGreaterThanOrEqual(4);
    expect(peakConcurrent).toBeLessThanOrEqual(5);
    runner.stop();
  });

  it("respects maxConcurrent=1 (serial execution)", async () => {
    const runner = new SessionRunner(store, trackingFactory, () => ({}), {
      maxConcurrent: 1,
    });
    await Promise.all([
      runner.spawn({ prompt: "a" }),
      runner.spawn({ prompt: "b" }),
      runner.spawn({ prompt: "c" }),
    ]);
    await new Promise((r) => setTimeout(r, 800));
    expect(peakConcurrent).toBe(1);
    expect(store.list({ status: "completed" })).toHaveLength(3);
    runner.stop();
  });
});
