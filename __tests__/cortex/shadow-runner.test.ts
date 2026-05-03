/**
 * StackOwl — Element 7 T14 — ShadowRunner
 *
 * Runs a candidate (rewritten) tool alongside the baseline for a window,
 * compares success rates, and emits a verdict (`promote` | `rollback` |
 * `continue`). Promotion: ≥100 candidate calls AND ≥5pp improvement.
 * Rollback: ≥100 candidate calls AND >5pp regression. Otherwise continue.
 *
 * Schema is v24 — adds `tool_evolution_runs` table (idempotent).
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ShadowRunner } from "../../src/tools/cortex/shadow-runner.js";

describe("ShadowRunner — auto-promote/rollback", () => {
  let db: MemoryDatabase;
  let runner: ShadowRunner;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "shadow-"));
    db = new MemoryDatabase(dir);
    runner = new ShadowRunner(db);
  });

  it("promotes when candidate is ≥5pp better over ≥100 calls", () => {
    const runId = runner.start({
      baselineTool: "web",
      candidateTool: "web__v2",
      baselinePath: "src/tools/web.ts",
      candidatePath: "src/tools/web__v2.ts",
    });
    // Baseline: 60% success (60 / 100). Candidate: 80% success (80 / 100).
    for (let i = 0; i < 100; i++) {
      runner.record(runId, { which: "baseline", success: i < 60 });
      runner.record(runId, { which: "candidate", success: i < 80 });
    }
    expect(runner.evaluate(runId)).toBe("promote");
  });

  it("rolls back when candidate is >5pp worse over ≥100 calls", () => {
    const runId = runner.start({
      baselineTool: "web",
      candidateTool: "web__v2",
      baselinePath: "src/tools/web.ts",
      candidatePath: "src/tools/web__v2.ts",
    });
    // Baseline: 80%. Candidate: 60%. -20pp regression.
    for (let i = 0; i < 100; i++) {
      runner.record(runId, { which: "baseline", success: i < 80 });
      runner.record(runId, { which: "candidate", success: i < 60 });
    }
    expect(runner.evaluate(runId)).toBe("rollback");
  });

  it("returns 'continue' before the call threshold is met", () => {
    const runId = runner.start({
      baselineTool: "web",
      candidateTool: "web__v2",
      baselinePath: "src/tools/web.ts",
      candidatePath: "src/tools/web__v2.ts",
    });
    for (let i = 0; i < 50; i++) {
      runner.record(runId, { which: "baseline", success: true });
      runner.record(runId, { which: "candidate", success: false });
    }
    // Only 50 candidate calls — below the ≥100 threshold even though regression
    // is huge. Verdict is "continue" until we have enough samples.
    expect(runner.evaluate(runId)).toBe("continue");
  });

  it("returns 'continue' when difference is within ±5pp", () => {
    const runId = runner.start({
      baselineTool: "web",
      candidateTool: "web__v2",
      baselinePath: "src/tools/web.ts",
      candidatePath: "src/tools/web__v2.ts",
    });
    // Baseline 75%, candidate 78%: +3pp, within the noise band.
    for (let i = 0; i < 100; i++) {
      runner.record(runId, { which: "baseline", success: i < 75 });
      runner.record(runId, { which: "candidate", success: i < 78 });
    }
    expect(runner.evaluate(runId)).toBe("continue");
  });

  it("persists run state so counters survive process boundaries", () => {
    const runId = runner.start({
      baselineTool: "web",
      candidateTool: "web__v2",
      baselinePath: "src/tools/web.ts",
      candidatePath: "src/tools/web__v2.ts",
    });
    runner.record(runId, { which: "baseline", success: true });
    runner.record(runId, { which: "candidate", success: false });

    const fresh = new ShadowRunner(db);
    const row = db.rawDb
      .prepare("SELECT * FROM tool_evolution_runs WHERE id = ?")
      .get(runId) as {
      baseline_total: number;
      baseline_successes: number;
      candidate_total: number;
      candidate_successes: number;
    };
    expect(row.baseline_total).toBe(1);
    expect(row.baseline_successes).toBe(1);
    expect(row.candidate_total).toBe(1);
    expect(row.candidate_successes).toBe(0);
    // Verdict via fresh instance still works
    expect(fresh.evaluate(runId)).toBe("continue");
  });
});
