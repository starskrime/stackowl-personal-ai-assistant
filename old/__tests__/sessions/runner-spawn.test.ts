import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";
import type { Session } from "../../src/sessions/types.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

function stubEngineFactory() {
  return {
    async run(prompt: string, _context: any) {
      return { content: `STUB:${prompt.slice(0, 40)}`, history: [] };
    },
  } as any;
}

function stubBaseContext() {
  return {} as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-spawn-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionRunner.spawn", () => {
  it("returns a session in pending then transitions through running to completed", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const session: Session = await runner.spawn({ prompt: "do a thing" });
    expect(session.status).toBe("pending");
    expect(session.id).toBeTruthy();
    await new Promise((r) => setTimeout(r, 200));
    const final = store.findOne(session.id);
    expect(final?.status).toBe("completed");
    expect(final?.result).toContain("STUB:do a thing");
    runner.stop();
  });

  it("multiple spawns get distinct ids and complete independently", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s1 = await runner.spawn({ prompt: "task A" });
    const s2 = await runner.spawn({ prompt: "task B" });
    expect(s1.id).not.toBe(s2.id);
    await new Promise((r) => setTimeout(r, 250));
    expect(store.findOne(s1.id)?.status).toBe("completed");
    expect(store.findOne(s2.id)?.status).toBe("completed");
    runner.stop();
  });

  it("metadata is persisted on the session row", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s = await runner.spawn({
      prompt: "with metadata",
      metadata: { owl: "Noctua", model: "claude-haiku-4-5-20251001" },
    });
    expect(store.findOne(s.id)?.metadata.owl).toBe("Noctua");
    runner.stop();
  });

  it("parentId is persisted when provided", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s = await runner.spawn({ prompt: "child", parentId: "parent-1" });
    expect(store.findOne(s.id)?.parentId).toBe("parent-1");
    runner.stop();
  });
});
