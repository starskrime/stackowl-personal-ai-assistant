import { describe, it, expect } from "vitest";
import { DAGPlanner, CircularDependencyError } from "../../src/context/dag-planner.js";
import type { ContextLayer } from "../../src/context/layer.js";

function makeLayer(name: string, produces: string[], dependsOn: string[], priority = 50): ContextLayer {
  return {
    name, priority, maxTokens: 100, produces, dependsOn,
    shouldFire: () => true,
    build: async () => name,
  };
}

describe("DAGPlanner", () => {
  it("puts independent layers in batch 0", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], []),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches[0]).toHaveLength(2);
    expect(batches).toHaveLength(1);
  });

  it("puts dependent layer in next batch", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], ["a"]),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches).toHaveLength(2);
    expect(batches[0][0].name).toBe("A");
    expect(batches[1][0].name).toBe("B");
  });

  it("sorts by priority ascending within batch", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], [], 80),
      makeLayer("B", ["b"], [], 10),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches[0][0].name).toBe("B");
    expect(batches[0][1].name).toBe("A");
  });

  it("throws CircularDependencyError on cycle", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], ["b"]),
      makeLayer("B", ["b"], ["a"]),
    ];
    expect(() => planner.buildBatches(layers)).toThrow(CircularDependencyError);
  });

  it("handles 3-level chain", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], ["a"]),
      makeLayer("C", ["c"], ["b"]),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches).toHaveLength(3);
  });
});
