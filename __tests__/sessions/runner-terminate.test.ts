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

function longRunningFactory() {
  return {
    async run(_prompt: string, context: any) {
      for (let i = 0; i < 100; i++) {
        if (context.signal?.aborted) {
          throw new DOMException("Aborted", "AbortError");
        }
        await new Promise(r => setTimeout(r, 20));
      }
      return { content: "should not reach" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-term-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner.terminate", () => {
  it("terminates a running session via AbortSignal", async () => {
    const runner = new SessionRunner(store, longRunningFactory, () => ({}));
    const s = await runner.spawn({ prompt: "long task" });
    await new Promise(r => setTimeout(r, 50));
    const result = runner.terminate(s.id);
    expect(result.terminated).toBe(true);
    expect(["running", "pending"]).toContain(result.previousStatus);
    await new Promise(r => setTimeout(r, 100));
    expect(store.findOne(s.id)?.status).toBe("terminated");
  });

  it("terminate is idempotent on terminal sessions", () => {
    const runner = new SessionRunner(store, longRunningFactory, () => ({}));
    store.create({
      id: "already-done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const result = runner.terminate("already-done");
    expect(result.terminated).toBe(true);
    expect(result.previousStatus).toBe("completed");
    runner.stop();
  });

  it("terminate on unknown session returns terminated:false", () => {
    const runner = new SessionRunner(store, longRunningFactory, () => ({}));
    const result = runner.terminate("nonexistent");
    expect(result.terminated).toBe(false);
    runner.stop();
  });
});
