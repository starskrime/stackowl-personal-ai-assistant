import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsStatusTool } from "../../../src/tools/sessions/sessions-status.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "done" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-status-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsStatusTool", () => {
  it("returns session metadata for existing id", async () => {
    const s = await runner.spawn({ prompt: "task X" });
    const res = await SessionsStatusTool.execute({ id: s.id }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.session.id).toBe(s.id);
    expect(parsed.data.session.prompt).toBe("task X");
  });

  it("returns NOT_FOUND for missing id", async () => {
    const res = await SessionsStatusTool.execute({ id: "ghost" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });

  it("include_messages=true includes pending messages", async () => {
    const s = await runner.spawn({ prompt: "x" });
    store.appendMessage(s.id, "from_session", "interim output");
    const res = await SessionsStatusTool.execute(
      { id: s.id, include_messages: true },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.messages).toBeDefined();
    expect(parsed.data.messages[0].content).toBe("interim output");
  });
});
