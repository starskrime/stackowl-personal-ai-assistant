import { describe, it, expect, vi, beforeEach } from "vitest";
import { RoutingRuleMonitor } from "../../src/cognition/routing-rule-monitor.js";
import { RoutingRuleStore } from "../../src/cognition/routing-rule-store.js";
import type { RoutingRule } from "../../src/cognition/routing-rule-store.js";
import type { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "routing-rule-monitor-test-"));
}

function makeRule(overrides: Partial<RoutingRule> = {}): RoutingRule {
  return {
    id: "web_search:search_query",
    failingTool: "web_search",
    intentPattern: "search query",
    suggestedAlternatives: ["web_fetch", "live_browser"],
    appliedAt: Date.now(),
    version: 1,
    disabled: false,
    observationCount: 0,
    successCount: 0,
    ...overrides,
  };
}

// A minimal mock EdgeAccumulator that doesn't need a real DB
function makeMockEdgeAccumulator(): EdgeAccumulator {
  return {
    observe: vi.fn(),
  } as unknown as EdgeAccumulator;
}

describe("RoutingRuleMonitor", () => {
  let tmpDir: string;
  let store: RoutingRuleStore;

  beforeEach(() => {
    tmpDir = makeTmpDir();
    store = new RoutingRuleStore(tmpDir);
  });

  it("recordOutcome increments observationCount and successCount", () => {
    const rule = makeRule();
    store.upsert(rule);

    const monitor = new RoutingRuleMonitor(store);
    monitor.recordOutcome("web_search", "search query", true);

    const updated = store.getById(rule.id);
    expect(updated).toBeDefined();
    expect(updated!.observationCount).toBe(1);
    expect(updated!.successCount).toBe(1);
  });

  it("after MONITOR_WINDOW observations with all failures, rule is disabled (regression rollback)", () => {
    const rule = makeRule();
    store.upsert(rule);

    const monitor = new RoutingRuleMonitor(store);

    // Record 5 failures
    for (let i = 0; i < 5; i++) {
      monitor.recordOutcome("web_search", "search query", false);
    }

    const updated = store.getById(rule.id);
    expect(updated).toBeDefined();
    expect(updated!.disabled).toBe(true);
    expect(updated!.observationCount).toBe(5);
    expect(updated!.successCount).toBe(0);
  });

  it("after MONITOR_WINDOW observations with all successes, rule stays active", () => {
    const rule = makeRule();
    store.upsert(rule);

    const monitor = new RoutingRuleMonitor(store);

    // Record 5 successes
    for (let i = 0; i < 5; i++) {
      monitor.recordOutcome("web_search", "search query", true);
    }

    const updated = store.getById(rule.id);
    expect(updated).toBeDefined();
    expect(updated!.disabled).toBe(false);
    expect(updated!.observationCount).toBe(5);
    expect(updated!.successCount).toBe(5);
  });

  it("after rollback, edgeAccumulator.observe is called with success: false", () => {
    const rule = makeRule();
    store.upsert(rule);

    const mockEdge = makeMockEdgeAccumulator();
    const monitor = new RoutingRuleMonitor(store, mockEdge);

    // Record 5 failures to trigger rollback
    for (let i = 0; i < 5; i++) {
      monitor.recordOutcome("web_search", "search query", false);
    }

    expect(mockEdge.observe).toHaveBeenCalledWith(
      expect.objectContaining({
        fromTool: "web_search",
        toTool: "web_fetch",
        success: false,
      })
    );
  });
});
