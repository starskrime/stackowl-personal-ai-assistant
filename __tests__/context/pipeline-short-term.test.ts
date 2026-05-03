// __tests__/context/pipeline-short-term.test.ts
import { describe, it, expect } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import type { TriageSignals, ContextRequest } from "../../src/context/layer.js";

function mockTriage(): TriageSignals {
  return { userMessage: "hi", isConversational: true, hasFrustration: false,
    isOpinionRequest: false, hasTemporalTrigger: false, isReturningUser: false,
    sessionDepth: 1, hasActiveItems: false, effectiveUserId: "u1", continuityClass: null };
}
function mockReq(): ContextRequest {
  return { session: { id: "s1" } as any, callbacks: {} as any,
    continuityResult: null, digest: null, deps: {} as any };
}

function makePipeline(layers = []) {
  return new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
}

describe("ContextPipeline.setShortTermLayer", () => {
  it("includes short-term layer content in run() output", async () => {
    const pipeline = makePipeline([]);
    pipeline.setShortTermLayer("parliament_synthesis", "Verdict: PROCEED — synthesis text", { priority: 117, ttlTurns: 3 });
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("Verdict: PROCEED");
  });

  it("decrements ttlTurns by 1 after each run()", async () => {
    const pipeline = makePipeline([]);
    pipeline.setShortTermLayer("test_layer", "ephemeral content", { priority: 50, ttlTurns: 2 });
    await pipeline.run(mockReq(), mockTriage());
    // Second run: still present (ttlTurns was 2, now 1)
    const { output: out2 } = await pipeline.run(mockReq(), mockTriage());
    expect(out2).toContain("ephemeral content");
    // Third run: expired (ttlTurns hit 0 after second run)
    const { output: out3 } = await pipeline.run(mockReq(), mockTriage());
    expect(out3).not.toContain("ephemeral content");
  });

  it("respects priority ordering — short-term layer with priority 117 is between pellets (115) and profile (120)", async () => {
    // Build static layers at 115 and 120
    const pelletsLayer = {
      name: "pellets", priority: 115, maxTokens: 500,
      produces: ["pellets"], dependsOn: [],
      shouldFire: () => true, build: async () => "PELLETS_CONTENT",
    };
    const profileLayer = {
      name: "profile", priority: 120, maxTokens: 500,
      produces: ["profile"], dependsOn: [],
      shouldFire: () => true, build: async () => "PROFILE_CONTENT",
    };
    const pipeline = new ContextPipeline(
      [profileLayer, pelletsLayer],
      new ContextCache(), new LayerHealthMonitor(), new DAGPlanner(),
    );
    pipeline.setShortTermLayer("parliament_synthesis", "PARLIAMENT_CONTENT", { priority: 117, ttlTurns: 1 });
    const { output } = await pipeline.run(mockReq(), mockTriage());
    const pelletsIdx = output.indexOf("PELLETS_CONTENT");
    const parliIdx   = output.indexOf("PARLIAMENT_CONTENT");
    const profileIdx = output.indexOf("PROFILE_CONTENT");
    expect(pelletsIdx).toBeLessThan(parliIdx);
    expect(parliIdx).toBeLessThan(profileIdx);
  });
});
