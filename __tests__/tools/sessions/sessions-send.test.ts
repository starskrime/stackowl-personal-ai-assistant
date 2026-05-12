import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsSendTool } from "../../../src/tools/sessions/sessions-send.js";

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
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-send-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, slowFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsSendTool", () => {
  it("queues a to_session message and reports accepted", async () => {
    const s = await runner.spawn({ prompt: "long task" });
    await new Promise(r => setTimeout(r, 50));
    const res = await SessionsSendTool.execute({ id: s.id, content: "interrupt" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.accepted).toBe(true);
    expect(parsed.data.queued_message_id).toBeGreaterThan(0);
  });

  it("returns accepted=false for terminal sessions", async () => {
    store.create({
      id: "done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const res = await SessionsSendTool.execute({ id: "done", content: "too late" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.accepted).toBe(false);
    expect(parsed.data.current_status).toBe("completed");
  });

  it("returns NOT_FOUND for unknown session", async () => {
    const res = await SessionsSendTool.execute({ id: "ghost", content: "x" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });
});
