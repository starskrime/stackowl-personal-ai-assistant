import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { SessionStateStore } from "../../src/routing/session-state.js";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("SessionStateStore", () => {
  let workspace: string;
  let store: SessionStateStore;

  beforeEach(async () => {
    workspace = await mkdtemp(join(tmpdir(), "session-state-test-"));
    store = new SessionStateStore(workspace);
  });

  afterEach(async () => {
    await rm(workspace, { recursive: true, force: true });
  });

  it("returns null when no state file exists", async () => {
    const state = await store.load("user123");
    expect(state).toBeNull();
  });

  it("saves and loads session state", async () => {
    await store.save("user123", { activeOwlName: "historyMan", pinnedAt: "2026-01-01T00:00:00Z" });
    const loaded = await store.load("user123");
    expect(loaded).not.toBeNull();
    expect(loaded?.activeOwlName).toBe("historyMan");
  });

  it("clear removes the state file", async () => {
    await store.save("user123", { activeOwlName: "historyMan", pinnedAt: "2026-01-01T00:00:00Z" });
    await store.clear("user123");
    const loaded = await store.load("user123");
    expect(loaded).toBeNull();
  });

  it("saves state for multiple users independently", async () => {
    await store.save("user1", { activeOwlName: "owlA", pinnedAt: "2026-01-01T00:00:00Z" });
    await store.save("user2", { activeOwlName: "owlB", pinnedAt: "2026-01-01T00:00:00Z" });
    const s1 = await store.load("user1");
    const s2 = await store.load("user2");
    expect(s1?.activeOwlName).toBe("owlA");
    expect(s2?.activeOwlName).toBe("owlB");
  });

  it("returns null for corrupt JSON", async () => {
    const { mkdir, writeFile } = await import("node:fs/promises");
    await mkdir(join(workspace, "sessions"), { recursive: true });
    await writeFile(join(workspace, "sessions", "user123.json"), "not json", "utf-8");
    const loaded = await store.load("user123");
    expect(loaded).toBeNull();
  });
});
