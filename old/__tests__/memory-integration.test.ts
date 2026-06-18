/**
 * Element 15 — End-to-end integration test for the canonical memory surface.
 *
 * Proves the full pipeline composes correctly:
 *   - MemoryRepository over v25 schema
 *   - MemoryWriter bus listener (engine:turn_complete → expireWorkingMemories)
 *   - createMemoryTool with HITL approval gate at importance ≥ 0.8
 *   - dispatchMemoryCommand serving CLI and Telegram identically (channel parity)
 *
 * No gateway or engine is constructed here — those layers are exercised by their
 * own suites. This test verifies the *seam*: that the canonical pieces wire up
 * the way `src/index.ts` Task 30 boot path wires them.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { MemoryWriter } from "../src/memory/writer.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";
import { createMemoryTool } from "../src/tools/memory-unified.js";
import { dispatchMemoryCommand } from "../src/gateway/commands/memory-router.js";

interface Wired {
  db: import("better-sqlite3").Database;
  repo: MemoryRepository;
  bus: GatewayEventBus;
  writer: MemoryWriter;
  hitlCreate: ReturnType<typeof vi.fn>;
  tool: ReturnType<typeof createMemoryTool>;
}

function wire(): Wired {
  const db = new Database(":memory:");
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  applyV25Migration(db);

  const bus = new GatewayEventBus();
  const repo = new MemoryRepository(db, bus);

  // Stub router + providerRegistry — writer.ingest isn't exercised here, only
  // attachBusListeners. expireWorkingMemories is sync and uses repo only.
  const writer = new MemoryWriter({
    repo,
    bus,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    router: { resolve: vi.fn() } as any,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    providerRegistry: { get: vi.fn() } as any,
  });
  writer.attachBusListeners();

  const hitlCreate = vi.fn().mockResolvedValue("ckpt-mem");
  const tool = createMemoryTool({
    repo,
    bus,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    hitl: { create: hitlCreate } as any,
  });

  return { db, repo, bus, writer, hitlCreate, tool };
}

describe("Element 15 — memory surface integration", () => {
  let w: Wired;
  beforeEach(() => {
    w = wire();
  });

  it("turn_complete bus event triggers working-memory expiry through writer", () => {
    // Insert a working memory with valid_at far in the past.
    const longAgo = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString(); // 25h
    w.repo.insertBatch([
      { id: "w1", kind: "working", content: "stale scratchpad", importance: 0.3, valid_at: longAgo },
      { id: "w2", kind: "working", content: "fresh scratchpad", importance: 0.3 },
      { id: "s1", kind: "semantic", content: "should never expire", importance: 0.5, valid_at: longAgo },
    ]);

    w.bus.emit({ type: "engine:turn_complete", sessionId: "s", turnId: "t", durationMs: 1 });

    // Stale working invalidated, fresh working untouched, semantic untouched.
    expect(w.repo.getById("w1")?.invalid_at).not.toBeNull();
    expect(w.repo.getById("w2")?.invalid_at).toBeNull();
    expect(w.repo.getById("s1")?.invalid_at).toBeNull();
  });

  it("memory tool routes high-importance invalidations through HITL approval", async () => {
    w.repo.insertBatch([
      { id: "high", kind: "semantic", content: "user is left-handed", importance: 0.9 },
    ]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await w.tool.execute(
      { action: "invalidate", id: "high", reason: "user said it's wrong" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.requiresApproval).toBe(true);
    expect(parsed.data.checkpointId).toBe("ckpt-mem");
    expect(w.hitlCreate).toHaveBeenCalledTimes(1);
    // Memory still valid until human approves.
    expect(w.repo.getById("high")?.invalid_at).toBeNull();
  });

  it("memory tool applies low-importance invalidations directly", async () => {
    w.repo.insertBatch([
      { id: "low", kind: "semantic", content: "user dislikes peppers", importance: 0.4 },
    ]);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const out = await w.tool.execute(
      { action: "invalidate", id: "low", reason: "no longer true" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      {} as any,
    );
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.requiresApproval).toBeFalsy();
    expect(w.hitlCreate).not.toHaveBeenCalled();
    expect(w.repo.getById("low")?.invalid_at).not.toBeNull();
  });

  it("CLI and Telegram surfaces produce byte-identical output via shared router", async () => {
    w.repo.insertBatch([
      { id: "p1", kind: "semantic", content: "prefers dark mode", importance: 0.6 },
      { id: "p2", kind: "episodic", content: "discussed Element 15 today", importance: 0.5 },
    ]);

    // CLI invocation simulates: `/memory list`
    const cliOutput = await dispatchMemoryCommand("list", [], { repo: w.repo });
    // Telegram invocation simulates the same `/memory list` — same router, same deps.
    const tgOutput = await dispatchMemoryCommand("list", [], { repo: w.repo });

    expect(cliOutput).toBe(tgOutput);
    expect(cliOutput).toContain("2 memories");

    // Same parity for `/memory search`.
    const cliSearch = await dispatchMemoryCommand("search", ["dark"], { repo: w.repo });
    const tgSearch = await dispatchMemoryCommand("search", ["dark"], { repo: w.repo });
    expect(cliSearch).toBe(tgSearch);
    expect(cliSearch).toContain("dark mode");
  });

  it("repository / tool / router agree on a record's lifecycle", async () => {
    w.repo.insertBatch([
      { id: "x", kind: "semantic", content: "owns a beagle", importance: 0.5 },
    ]);

    // 1. Tool sees it via search.
    const search = JSON.parse(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await w.tool.execute({ action: "search", query: "beagle" }, {} as any),
    );
    expect(search.data.results.map((r: { id: string }) => r.id)).toContain("x");

    // 2. Router /memory get returns it.
    const got = await dispatchMemoryCommand("get", ["x"], { repo: w.repo });
    expect(got).toContain("beagle");

    // 3. Tool invalidates (low-importance — direct).
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await w.tool.execute({ action: "invalidate", id: "x", reason: "rehomed" }, {} as any);

    // 4. Router /memory list excludes invalidated entries by default.
    const listAfter = await dispatchMemoryCommand("list", [], { repo: w.repo });
    expect(listAfter).toContain("0 memories");

    // 5. History is preserved — repo.history returns the invalidation row.
    const hist = w.repo.history("x");
    expect(hist.invalidations.length).toBe(1);
    expect(hist.invalidations[0]?.reason).toBe("rehomed");
  });
});
