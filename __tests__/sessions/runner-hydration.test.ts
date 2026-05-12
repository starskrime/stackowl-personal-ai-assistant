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

function stubEngineFactory() {
  return {
    async run(prompt: string, _context: any) {
      return { content: `RESUMED:${prompt}` };
    },
  } as any;
}

function stubBaseContext() {
  return {} as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-hyd-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionRunner.start (hydration)", () => {
  it("resumes a running session from store on start", async () => {
    const now = new Date().toISOString();
    store.create({
      id: "left-running",
      parentId: null,
      status: "running",
      prompt: "do work",
      history: [],
      metadata: {},
      createdAt: now,
      updatedAt: now,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 300));
    expect(store.findOne("left-running")?.status).toBe("completed");
    runner.stop();
  });

  it("resumes a pending session from store on start", async () => {
    const now = new Date().toISOString();
    store.create({
      id: "left-pending",
      parentId: null,
      status: "pending",
      prompt: "unstarted work",
      history: [],
      metadata: {},
      createdAt: now,
      updatedAt: now,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 300));
    expect(store.findOne("left-pending")?.status).toBe("completed");
    runner.stop();
  });

  it("leaves awaiting_input sessions alone (no auto-resume)", async () => {
    store.create({
      id: "waiting",
      parentId: null,
      status: "awaiting_input",
      prompt: "x",
      history: [],
      metadata: {},
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    expect(store.findOne("waiting")?.status).toBe("awaiting_input");
    runner.stop();
  });

  it("terminal sessions are not touched", async () => {
    store.create({
      id: "done",
      parentId: null,
      status: "completed",
      prompt: "x",
      history: [],
      metadata: {},
      result: "old result",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    expect(store.findOne("done")?.result).toBe("old result");
    runner.stop();
  });

  it("hydrates multiple non-terminal sessions in parallel", async () => {
    const now = new Date().toISOString();
    store.create({
      id: "task-1",
      parentId: null,
      status: "pending",
      prompt: "work 1",
      history: [],
      metadata: {},
      createdAt: now,
      updatedAt: now,
    });
    store.create({
      id: "task-2",
      parentId: null,
      status: "running",
      prompt: "work 2",
      history: [],
      metadata: {},
      createdAt: now,
      updatedAt: now,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 400));
    expect(store.findOne("task-1")?.status).toBe("completed");
    expect(store.findOne("task-2")?.status).toBe("completed");
    runner.stop();
  });
});
