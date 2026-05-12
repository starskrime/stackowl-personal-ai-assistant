import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import type { Session } from "../../src/sessions/types.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-session-store-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

function makeSession(over: Partial<Session> = {}): Session {
  return {
    id: "s" + Math.random().toString(36).slice(2, 8),
    parentId: null,
    status: "pending",
    prompt: "test",
    history: [],
    metadata: {},
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...over,
  };
}

describe("SessionStore", () => {
  it("create + findOne roundtrip", () => {
    const s = makeSession({ id: "a", prompt: "hello" });
    store.create(s);
    const found = store.findOne("a");
    expect(found?.id).toBe("a");
    expect(found?.prompt).toBe("hello");
  });

  it("findOne returns null when not present", () => {
    expect(store.findOne("nope")).toBeNull();
  });

  it("update patches status + result and bumps updatedAt", () => {
    const s = makeSession({ id: "a" });
    store.create(s);
    const before = store.findOne("a")!;
    store.update("a", { status: "completed", result: "done" });
    const after = store.findOne("a")!;
    expect(after.status).toBe("completed");
    expect(after.result).toBe("done");
    expect(after.updatedAt >= before.updatedAt).toBe(true);
  });

  it("list filters by status", () => {
    store.create(makeSession({ id: "a", status: "running" }));
    store.create(makeSession({ id: "b", status: "completed" }));
    store.create(makeSession({ id: "c", status: "running" }));
    expect(store.list({ status: "running" })).toHaveLength(2);
  });

  it("list filters by parentId", () => {
    store.create(makeSession({ id: "p", parentId: null }));
    store.create(makeSession({ id: "c1", parentId: "p" }));
    store.create(makeSession({ id: "c2", parentId: "p" }));
    expect(store.list({ parentId: "p" })).toHaveLength(2);
  });

  it("appendMessage + pendingMessages roundtrip", () => {
    store.create(makeSession({ id: "a" }));
    const m1 = store.appendMessage("a", "to_session", "hello");
    const m2 = store.appendMessage("a", "to_session", "world");
    const pending = store.pendingMessages("a", "to_session");
    expect(pending.map((m) => m.content)).toEqual(["hello", "world"]);
    expect(pending[0].id).toBe(m1.id);
    expect(pending[1].id).toBe(m2.id);
  });

  it("markConsumed removes from pending", () => {
    store.create(makeSession({ id: "a" }));
    const m = store.appendMessage("a", "to_session", "x");
    store.markConsumed(m.id);
    expect(store.pendingMessages("a", "to_session")).toHaveLength(0);
  });

  it("history round-trips through JSON", () => {
    const s = makeSession({
      id: "a",
      history: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "there" },
      ],
    });
    store.create(s);
    const found = store.findOne("a")!;
    expect(found.history).toEqual(s.history);
  });
});
