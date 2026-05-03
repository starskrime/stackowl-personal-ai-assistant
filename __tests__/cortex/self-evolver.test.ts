/**
 * StackOwl — Element 7 T13 — SelfEvolver scaffolding + critical-tool exclusion
 *
 * SelfEvolver picks the worst-performing non-critical tool over the recent
 * window and is the orchestration entry point for the SET (Self-Evolving
 * Tools) loop. The critical-tool exclusion list MUST be enforced at
 * candidate selection — never as a post-filter — because rewriting `remember`,
 * `write_file`, or `shell` could destroy user data.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import {
  SelfEvolver,
  CRITICAL_TOOLS,
} from "../../src/tools/cortex/self-evolver.js";

describe("SelfEvolver — critical exclusion + candidate selection", () => {
  let db: MemoryDatabase;
  let evolver: SelfEvolver;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "set-"));
    db = new MemoryDatabase(dir);
    evolver = new SelfEvolver({
      db,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      patchTool: { execute: async () => "" } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      hitlChannel: { propose: async () => null } as any,
    });

    // Seed: low-success non-critical tool ("web") and an even-lower-success
    // critical tool ("remember") that MUST be skipped despite being the worst.
    for (let i = 0; i < 100; i++) {
      db.recordToolExecution({
        toolName: "web",
        success: i < 30,
        durationMs: 100,
      });
    }
    for (let i = 0; i < 100; i++) {
      db.recordToolExecution({
        toolName: "remember",
        success: i < 10,
        durationMs: 100,
      });
    }
  });

  it("returns the worst-performing non-critical tool", async () => {
    const candidate = await evolver.findCandidate({ days: 7 });
    expect(candidate?.toolName).toBe("web");
    expect(candidate?.successRate).toBeCloseTo(0.3, 2);
    expect(candidate?.failureCount).toBe(70);
  });

  it("never selects a critical tool even if it is the worst", async () => {
    const candidate = await evolver.findCandidate({ days: 7 });
    expect(candidate?.toolName).not.toBe("remember");
  });

  it("declares core durable-state tools as critical", () => {
    expect(CRITICAL_TOOLS.has("remember")).toBe(true);
    expect(CRITICAL_TOOLS.has("write_file")).toBe(true);
    expect(CRITICAL_TOOLS.has("shell")).toBe(true);
    expect(CRITICAL_TOOLS.has("patch_tool")).toBe(true);
  });

  it("respects the minExecutions threshold", async () => {
    const cleanDir = mkdtempSync(join(tmpdir(), "set-clean-"));
    const cleanDb = new MemoryDatabase(cleanDir);
    const ev = new SelfEvolver({
      db: cleanDb,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      patchTool: { execute: async () => "" } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      hitlChannel: { propose: async () => null } as any,
    });
    for (let i = 0; i < 5; i++) {
      cleanDb.recordToolExecution({
        toolName: "web",
        success: false,
        durationMs: 100,
      });
    }
    const candidate = await ev.findCandidate({ minExecutions: 20 });
    expect(candidate).toBeNull();
  });

  it("returns null when there are no executions in the window", async () => {
    const cleanDir = mkdtempSync(join(tmpdir(), "set-empty-"));
    const cleanDb = new MemoryDatabase(cleanDir);
    const ev = new SelfEvolver({
      db: cleanDb,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      patchTool: { execute: async () => "" } as any,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      hitlChannel: { propose: async () => null } as any,
    });
    expect(await ev.findCandidate()).toBeNull();
  });
});
