import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SubagentsTool } from "../../../src/tools/sessions/subagents.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "done" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-subagents-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SubagentsTool", () => {
  it("spawns N sessions and returns their ids", async () => {
    const res = await SubagentsTool.execute(
      { tasks: ["task A", "task B", "task C"] },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.spawned).toBe(3);
    expect(parsed.data.sessions).toHaveLength(3);
    expect(parsed.data.sessions[0].status).toBe("pending");
  });

  it("returns error when tasks is empty", async () => {
    const res = await SubagentsTool.execute({ tasks: [] }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("MISSING_ARG");
  });

  it("shared_context is prepended to every task prompt", async () => {
    const res = await SubagentsTool.execute(
      { tasks: ["do X"], shared_context: "Context: project Foo" },
      {} as any,
    );
    const parsed = JSON.parse(res);
    const sessionId = parsed.data.sessions[0].id;
    const session = store.findOne(sessionId);
    expect(session?.prompt).toContain("Context: project Foo");
    expect(session?.prompt).toContain("do X");
  });
});
