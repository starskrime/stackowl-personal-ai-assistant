/**
 * StackOwl — Element 7 T15 — SelfEvolver.runOnce orchestration
 *
 * runOnce drives a full SET cycle: concurrency lock → candidate selection →
 * failure-trace pull → HITL gate → patchTool rewrite → ShadowRunner start.
 * The ImprovementScheduler invokes this weekly.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SelfEvolver } from "../../src/tools/cortex/self-evolver.js";
import { ShadowRunner } from "../../src/tools/cortex/shadow-runner.js";

interface PatchCall {
  toolPath: string;
  instruction: string;
  failureTraces: string[];
}

function makeDeps(opts: {
  db: MemoryDatabase;
  hitlApprove: boolean | null;
  patchReturn?: string;
}) {
  const patchCalls: PatchCall[] = [];
  const hitlMessages: string[] = [];
  return {
    patchCalls,
    hitlMessages,
    deps: {
      db: opts.db,
      patchTool: {
        async execute(args: PatchCall): Promise<string> {
          patchCalls.push(args);
          return opts.patchReturn ?? "src/tools/web__v2.ts";
        },
      },
      hitlChannel: {
        async propose(msg: string) {
          hitlMessages.push(msg);
          if (opts.hitlApprove === null) return null;
          return { approved: opts.hitlApprove };
        },
      },
      resolveToolPath: (name: string) =>
        name === "web" ? "src/tools/web.ts" : null,
    },
  };
}

function seedFailingTool(db: MemoryDatabase, name: string, count: number) {
  const successThreshold = Math.floor(count * 0.3);
  for (let i = 0; i < count; i++) {
    const success = i < successThreshold;
    db.recordToolExecution({
      toolName: name,
      success,
      durationMs: 100,
      ...(success ? {} : { errorMessage: `boom ${i}` }),
    });
  }
}

describe("SelfEvolver.runOnce — full SET cycle", () => {
  let db: MemoryDatabase;
  let runner: ShadowRunner;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "set-runonce-"));
    db = new MemoryDatabase(dir);
    runner = new ShadowRunner(db);
  });

  it("starts a shadow run when HITL approves", async () => {
    seedFailingTool(db, "web", 100);
    const { deps, patchCalls, hitlMessages } = makeDeps({
      db,
      hitlApprove: true,
    });
    const evolver = new SelfEvolver(deps);

    const result = await evolver.runOnce(runner);
    expect(result).not.toBeNull();
    expect(result!.toolName).toBe("web");
    expect(typeof result!.runId).toBe("number");
    expect(patchCalls).toHaveLength(1);
    expect(patchCalls[0]?.toolPath).toBe("src/tools/web.ts");
    expect(patchCalls[0]?.failureTraces.length).toBeGreaterThan(0);
    expect(hitlMessages).toHaveLength(1);
    expect(hitlMessages[0]).toContain("web");
  });

  it("returns null and does not patch when HITL declines", async () => {
    seedFailingTool(db, "web", 100);
    const { deps, patchCalls } = makeDeps({ db, hitlApprove: false });
    const evolver = new SelfEvolver(deps);

    const result = await evolver.runOnce(runner);
    expect(result).toBeNull();
    expect(patchCalls).toHaveLength(0);
  });

  it("returns null when no candidate exists", async () => {
    const { deps, patchCalls, hitlMessages } = makeDeps({
      db,
      hitlApprove: true,
    });
    const evolver = new SelfEvolver(deps);

    const result = await evolver.runOnce(runner);
    expect(result).toBeNull();
    expect(patchCalls).toHaveLength(0);
    expect(hitlMessages).toHaveLength(0);
  });

  it("never runs two evolutions concurrently — active run blocks new starts", async () => {
    seedFailingTool(db, "web", 100);

    // Pre-populate an active run to simulate one already in flight.
    runner.start({
      baselineTool: "other",
      candidateTool: "other__v2",
      baselinePath: "src/tools/other.ts",
      candidatePath: "src/tools/other__v2.ts",
    });

    const { deps, patchCalls } = makeDeps({ db, hitlApprove: true });
    const evolver = new SelfEvolver(deps);

    const result = await evolver.runOnce(runner);
    expect(result).toBeNull();
    expect(patchCalls).toHaveLength(0);
  });

  it("skips when the resolver cannot locate the tool's source path", async () => {
    seedFailingTool(db, "mystery", 100);
    const { deps, patchCalls, hitlMessages } = makeDeps({
      db,
      hitlApprove: true,
    });
    const evolver = new SelfEvolver(deps);

    const result = await evolver.runOnce(runner);
    expect(result).toBeNull();
    expect(patchCalls).toHaveLength(0);
    expect(hitlMessages).toHaveLength(0);
  });
});
