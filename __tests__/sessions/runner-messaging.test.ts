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

function fastFactory() {
  return {
    async run(prompt: string) {
      return { content: `OK:${prompt}` };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-msg-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionRunner messaging", () => {
  it("enqueueMessage stores a to_session row visible via store.pendingMessages", async () => {
    const runner = new SessionRunner(store, fastFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });
    runner.enqueueMessage(s.id, "follow-up");
    const pending = store.pendingMessages(s.id, "to_session");
    expect(pending).toHaveLength(1);
    expect(pending[0].content).toBe("follow-up");
    runner.stop();
  });

  it("awaitNextEvent returns immediately when new from_session messages exist", async () => {
    const runner = new SessionRunner(store, fastFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });
    await new Promise((r) => setTimeout(r, 150)); // let stub complete
    store.appendMessage(s.id, "from_session", "test-output");
    const result = await runner.awaitNextEvent(s.id, 2000);
    expect(result.ready).toBe(true);
    expect(result.newMessages.length).toBeGreaterThanOrEqual(1);
    runner.stop();
  });

  it("awaitNextEvent returns with terminal status when session completed", async () => {
    const runner = new SessionRunner(store, fastFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });
    await new Promise((r) => setTimeout(r, 200));
    const result = await runner.awaitNextEvent(s.id, 300);
    expect(result.status).toBe("completed");
    runner.stop();
  });
});
