import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsListTool } from "../../../src/tools/sessions/sessions-list.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "ok" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-list-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsListTool", () => {
  it("returns all sessions when no filter", async () => {
    store.create({ id: "a", parentId: null, status: "running", prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "b", parentId: null, status: "completed", prompt: "y", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({}, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions.length).toBeGreaterThanOrEqual(2);
  });

  it("filters by status", async () => {
    store.create({ id: "a", parentId: null, status: "running", prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "b", parentId: null, status: "completed", prompt: "y", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({ status: "running" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions).toHaveLength(1);
    expect(parsed.data.sessions[0].id).toBe("a");
  });

  it("filters by parent_id", async () => {
    store.create({ id: "p", parentId: null, status: "completed", prompt: "p", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "c", parentId: "p", status: "running", prompt: "c", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({ parent_id: "p" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions).toHaveLength(1);
    expect(parsed.data.sessions[0].id).toBe("c");
  });
});
