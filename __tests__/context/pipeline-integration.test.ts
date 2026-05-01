import { describe, it, expect } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import { computeTriage } from "../../src/context/triage.js";
import { SynthesisIdentityLayer } from "../../src/context/layers/identity.js";
import { WorkingMemoryDigestLayer } from "../../src/context/layers/working-memory.js";
import { TemporalAwarenessLayer } from "../../src/context/layers/infrastructure.js";

describe("ContextPipeline integration", () => {
  it("runs identity + working memory + temporal in correct batch order", async () => {
    const layers = [
      new SynthesisIdentityLayer(),
      new WorkingMemoryDigestLayer(),
      new TemporalAwarenessLayer(),
    ];
    const pipeline = new ContextPipeline(
      layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner()
    );

    const triage = computeTriage({
      userMessage: "help me debug this",
      sessionDepth: 2,
      continuityClass: null,
      userId: "u1",
      sessionId: "s1",
      hasActiveItems: false,
    });

    const req: any = {
      session: { id: "s1", owlName: "Atlas", owlPersonality: "You are Atlas.",
        messages: [{ role: "user", content: "help me debug this" }] },
      callbacks: {},
      channelId: "cli",
      userId: "u1",
      continuityResult: null,
      digest: { sessionId: "s1", task: "debug issue", artifacts: [], decisions: [], failed: [], openQuestions: [], updatedAt: new Date().toISOString() },
      deps: { pelletStore: null, memoryBus: null, sessionStore: null, eventBus: null, config: {}, intelligenceRouter: null },
    };

    const { output, trace } = await pipeline.run(req, triage);

    expect(output).toContain("Atlas");
    expect(output).toContain("temporal");
    expect(trace.length).toBe(3);
    expect(trace.filter((t) => t.fired).length).toBeGreaterThanOrEqual(2);
  });

  it("budget ceiling prevents context overflow", async () => {
    const layer: any = {
      name: "Big", priority: 10, maxTokens: 9000, produces: ["big"], dependsOn: [],
      shouldFire: () => true, build: async () => "x".repeat(40000),
    };
    const pipeline = new ContextPipeline(
      [layer], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner()
    );
    const triage = computeTriage({ userMessage: "hi", sessionDepth: 0, continuityClass: null,
      userId: "u1", sessionId: "s1", hasActiveItems: false });
    const { output } = await pipeline.run(
      { session: { id: "s1" }, callbacks: {}, continuityResult: null, digest: null, deps: {} } as any,
      triage,
      { globalTokenCeiling: 100 }
    );
    expect(output.length).toBeLessThan(600);
  });
});
