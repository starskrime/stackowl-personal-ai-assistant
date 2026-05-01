import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { HitlCheckpointStore, CliHitlChannel } from "../src/engine/hitl.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { HitlRequest, TaskLedger } from "../src/engine/types.js";

let dir: string, db: MemoryDatabase, store: HitlCheckpointStore;

const makeLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "simple", estimatedTurns: 1, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-hitl-"));
  db = new MemoryDatabase(dir);
  store = new HitlCheckpointStore(db);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("HitlCheckpointStore", () => {
  it("creates and loads a checkpoint", async () => {
    const req: HitlRequest = {
      kind: "approval",
      memo: { whatIDid: "searched for X", whatINeed: "confirmation to proceed" },
      ledgerSnapshot: makeLedger(),
      pendingAction: "delete file",
    };
    const id = await store.create("s1", "l1", req, 24 * 60);
    expect(id).toBeTruthy();
    const cp = await store.load(id);
    expect(cp?.requestKind).toBe("approval");
    expect(cp?.status).toBe("waiting");
  });

  it("resolves a checkpoint with response", async () => {
    const req: HitlRequest = {
      kind: "clarification",
      memo: { whatIDid: "analyzed", whatINeed: "which format?" },
      ledgerSnapshot: makeLedger(),
      pendingAction: "generate report",
    };
    const id = await store.create("s1", "l1", req, 60);
    await store.resolve(id, { approved: true, timedOut: false, freeText: "PDF please" });
    const cp = await store.load(id);
    expect(cp?.status).toBe("resolved");
    expect(cp?.response?.freeText).toBe("PDF please");
  });

  it("getWaiting returns pending checkpoints for session", async () => {
    const req: HitlRequest = {
      kind: "choice",
      memo: { whatIDid: "found options", whatINeed: "pick one", options: ["A","B"] },
      ledgerSnapshot: makeLedger(),
      pendingAction: "use option",
    };
    await store.create("s1", "l1", req, 60);
    const waiting = await store.getWaiting("s1");
    expect(waiting.length).toBe(1);
  });

  it("load returns null for unknown id", async () => {
    const result = await store.load("nonexistent-id");
    expect(result).toBeNull();
  });

  it("getWaiting does not return checkpoints from other sessions", async () => {
    const req: HitlRequest = {
      kind: "approval",
      memo: { whatIDid: "did something", whatINeed: "approval" },
      ledgerSnapshot: makeLedger(),
      pendingAction: "proceed",
    };
    await store.create("s1", "l1", req, 60);
    const waitingForS2 = await store.getWaiting("s2");
    expect(waitingForS2.length).toBe(0);
  });

  it("ledgerSnapshot survives round-trip", async () => {
    const ledger = makeLedger();
    const req: HitlRequest = {
      kind: "approval",
      memo: { whatIDid: "searched", whatINeed: "confirmation" },
      ledgerSnapshot: ledger,
      pendingAction: "delete",
    };
    const id = await store.create("s1", "l1", req, 60);
    const cp = await store.load(id);
    expect(cp?.ledgerSnapshot?.id).toBe("l1");
    expect(cp?.ledgerSnapshot?.goal).toBe("test");
  });
});
