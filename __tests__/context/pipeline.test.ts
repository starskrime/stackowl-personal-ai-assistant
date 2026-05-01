import { describe, it, expect, vi } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import type { ContextLayer, TriageSignals, ContextRequest, LayerResults } from "../../src/context/layer.js";

function mockTriage(): TriageSignals {
  return { userMessage: "hi", isConversational: true, hasFrustration: false,
    isOpinionRequest: false, hasTemporalTrigger: false, isReturningUser: false,
    sessionDepth: 1, hasActiveItems: false, effectiveUserId: "u1", continuityClass: null };
}
function mockReq(): ContextRequest {
  return { session: { id: "s1" } as any, callbacks: {} as any,
    continuityResult: null, digest: null, deps: {} as any };
}
function makeLayer(name: string, produces: string[], dependsOn: string[], output: string, priority = 50): ContextLayer {
  return { name, priority, maxTokens: 500, produces, dependsOn,
    shouldFire: () => true,
    build: async () => output };
}

describe("ContextPipeline", () => {
  it("runs all layers and concatenates output", async () => {
    const layers = [makeLayer("A", ["a"], [], "hello "), makeLayer("B", ["b"], [], "world")];
    const pipeline = new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("hello");
    expect(output).toContain("world");
  });

  it("skips layer when shouldFire returns false", async () => {
    const layer: ContextLayer = { name: "Skip", priority: 10, maxTokens: 100,
      produces: ["x"], dependsOn: [], shouldFire: () => false, build: async () => "SHOULD_NOT_APPEAR" };
    const pipeline = new ContextPipeline([layer], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output, trace } = await pipeline.run(mockReq(), mockTriage());
    expect(output).not.toContain("SHOULD_NOT_APPEAR");
    expect(trace[0].fired).toBe(false);
    expect(trace[0].skippedReason).toBe("shouldFire=false");
  });

  it("isolates layer error — other layers still run", async () => {
    const bad: ContextLayer = { name: "Bad", priority: 10, maxTokens: 100,
      produces: ["bad"], dependsOn: [], shouldFire: () => true, build: async () => { throw new Error("boom"); } };
    const good = makeLayer("Good", ["good"], [], "good output", 20);
    const pipeline = new ContextPipeline([bad, good], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("good output");
  });

  it("returns cache hit on second run", async () => {
    const buildFn = vi.fn(async () => "cached value");
    const layer: ContextLayer = { name: "Cached", priority: 10, maxTokens: 100,
      produces: ["c"], dependsOn: [], shouldFire: () => true, build: buildFn,
      getCacheKey: () => "stable-key" };
    const cache = new ContextCache();
    const pipeline = new ContextPipeline([layer], cache, new LayerHealthMonitor(), new DAGPlanner());
    await pipeline.run(mockReq(), mockTriage());
    const { trace } = await pipeline.run(mockReq(), mockTriage());
    expect(buildFn).toHaveBeenCalledTimes(1);
    expect(trace[0].cacheHit).toBe(true);
  });

  it("includes trace entry per layer", async () => {
    const layers = [makeLayer("A", ["a"], [], "a"), makeLayer("B", ["b"], [], "b")];
    const pipeline = new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { trace } = await pipeline.run(mockReq(), mockTriage());
    expect(trace).toHaveLength(2);
  });
});
