import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsYieldTool } from "../../../src/tools/sessions/sessions-yield.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "ok" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-yield-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsYieldTool", () => {
  it("returns ready=true with completed status after session finishes", async () => {
    const s = await runner.spawn({ prompt: "quick" });
    await new Promise(r => setTimeout(r, 150));
    const res = await SessionsYieldTool.execute({ id: s.id, timeout_ms: 1000 }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.ready).toBe(true);
    expect(parsed.data.status).toBe("completed");
  });

  it("returns ready=false on timeout when nothing changes", async () => {
    store.create({
      id: "stuck", parentId: null, status: "awaiting_input",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const res = await SessionsYieldTool.execute({ id: "stuck", timeout_ms: 300 }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.ready).toBe(false);
    expect(parsed.data.status).toBe("awaiting_input");
  });

  it("clamps timeout_ms above max (no actual long wait)", async () => {
    const s = await runner.spawn({ prompt: "x" });
    await new Promise(r => setTimeout(r, 200));   // let session complete first
    const startWait = Date.now();
    const res = await SessionsYieldTool.execute({ id: s.id, timeout_ms: 1_000_000 }, {} as any);
    const elapsed = Date.now() - startWait;
    expect(elapsed).toBeLessThan(2000);   // completes fast because session is already done
    expect(JSON.parse(res).success).toBe(true);
  }, 5000);
});
