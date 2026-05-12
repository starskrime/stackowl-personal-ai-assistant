import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsTerminateTool } from "../../../src/tools/sessions/sessions-terminate.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function slowFactory() {
  return {
    async run(_prompt: string, ctx: any) {
      for (let i = 0; i < 50; i++) {
        if (ctx.signal?.aborted) throw new DOMException("Aborted", "AbortError");
        await new Promise(r => setTimeout(r, 20));
      }
      return { content: "done" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-term-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, slowFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsTerminateTool", () => {
  it("terminates a running session", async () => {
    const s = await runner.spawn({ prompt: "long" });
    await new Promise(r => setTimeout(r, 50));
    const res = await SessionsTerminateTool.execute({ id: s.id }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.terminated).toBe(true);
    expect(parsed.data.previous_status).toMatch(/running|pending/);
  });

  it("is idempotent on terminal sessions", async () => {
    store.create({ id: "done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsTerminateTool.execute({ id: "done" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.terminated).toBe(true);
    expect(parsed.data.previous_status).toBe("completed");
  });

  it("returns terminated=false for unknown session", async () => {
    const res = await SessionsTerminateTool.execute({ id: "ghost" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.terminated).toBe(false);
  });
});
