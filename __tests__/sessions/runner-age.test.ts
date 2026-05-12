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
      return { content: `DONE:${prompt}` };
    },
  } as any;
}

function stubBaseContext() {
  return {} as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-age-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionRunner age-based auto-terminate", () => {
  it("terminates sessions older than sessionMaxAgeDays on start", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old",
      parentId: null,
      status: "running",
      prompt: "ancient",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    const s = store.findOne("old");
    expect(s?.status).toBe("terminated");
    expect(s?.error).toMatch(/auto-terminated|too old/);
    runner.stop();
  });

  it("leaves recent sessions alone (allows them to complete normally)", async () => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    store.create({
      id: "recent",
      parentId: null,
      status: "running",
      prompt: "recent",
      history: [],
      metadata: {},
      createdAt: oneHourAgo,
      updatedAt: oneHourAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 200));
    const s = store.findOne("recent");
    expect(s?.status).toBe("completed");
    runner.stop();
  });

  it("uses default 7 days when sessionMaxAgeDays not specified", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old-default",
      parentId: null,
      status: "pending",
      prompt: "ancient",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    const s = store.findOne("old-default");
    expect(s?.status).toBe("terminated");
    runner.stop();
  });

  it("auto-terminates pending sessions that are too old", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old-pending",
      parentId: null,
      status: "pending",
      prompt: "ancient pending",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    const s = store.findOne("old-pending");
    expect(s?.status).toBe("terminated");
    runner.stop();
  });

  it("auto-terminates awaiting_input sessions that are too old", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old-awaiting",
      parentId: null,
      status: "awaiting_input",
      prompt: "ancient awaiting",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    const s = store.findOne("old-awaiting");
    expect(s?.status).toBe("terminated");
    runner.stop();
  });

  it("does not touch terminal sessions regardless of age", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old-completed",
      parentId: null,
      status: "completed",
      prompt: "ancient",
      history: [],
      metadata: {},
      result: "old result",
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 100));
    const s = store.findOne("old-completed");
    expect(s?.status).toBe("completed");
    expect(s?.result).toBe("old result");
    runner.stop();
  });

  it("terminates multiple old sessions and resumes recent ones", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();

    // Two old sessions
    store.create({
      id: "old-1",
      parentId: null,
      status: "pending",
      prompt: "old 1",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });
    store.create({
      id: "old-2",
      parentId: null,
      status: "running",
      prompt: "old 2",
      history: [],
      metadata: {},
      createdAt: eightDaysAgo,
      updatedAt: eightDaysAgo,
    });

    // One recent session
    store.create({
      id: "recent-1",
      parentId: null,
      status: "pending",
      prompt: "recent",
      history: [],
      metadata: {},
      createdAt: oneHourAgo,
      updatedAt: oneHourAgo,
    });

    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext, {
      sessionMaxAgeDays: 7,
    });
    await runner.start();
    await new Promise((r) => setTimeout(r, 300));

    expect(store.findOne("old-1")?.status).toBe("terminated");
    expect(store.findOne("old-2")?.status).toBe("terminated");
    expect(store.findOne("recent-1")?.status).toBe("completed");

    runner.stop();
  });
});
