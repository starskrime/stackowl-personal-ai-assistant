import { describe, it, expect, beforeEach } from "vitest";
import { RoutingRuleStore } from "../../src/cognition/routing-rule-store.js";
import type { RoutingRule } from "../../src/cognition/routing-rule-store.js";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function makeTmpDir(): string {
  return mkdtempSync(join(tmpdir(), "routing-rule-store-test-"));
}

function makeRule(overrides: Partial<RoutingRule> = {}): RoutingRule {
  return {
    id: "web_search:price_amazon",
    failingTool: "web_search",
    intentPattern: "price amazon",
    suggestedAlternatives: ["web_fetch", "live_browser"],
    appliedAt: Date.now(),
    version: 1,
    disabled: false,
    observationCount: 0,
    successCount: 0,
    ...overrides,
  };
}

describe("RoutingRuleStore", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = makeTmpDir();
  });

  it("upsert + getById round-trips correctly", () => {
    const store = new RoutingRuleStore(tmpDir);
    const rule = makeRule();
    store.upsert(rule);

    const retrieved = store.getById(rule.id);
    expect(retrieved).toBeDefined();
    expect(retrieved!.id).toBe(rule.id);
    expect(retrieved!.failingTool).toBe("web_search");
    expect(retrieved!.suggestedAlternatives).toEqual(["web_fetch", "live_browser"]);
    expect(retrieved!.disabled).toBe(false);
  });

  it("getActive() excludes disabled rules", () => {
    const store = new RoutingRuleStore(tmpDir);
    const active = makeRule({ id: "active-rule", failingTool: "web_search", disabled: false });
    const disabled = makeRule({ id: "disabled-rule", failingTool: "web_fetch", disabled: true });
    store.upsert(active);
    store.upsert(disabled);

    const activeRules = store.getActive();
    expect(activeRules).toHaveLength(1);
    expect(activeRules[0].id).toBe("active-rule");
  });

  it("buildHint returns empty string when no active rules match", () => {
    const store = new RoutingRuleStore(tmpDir);
    const rule = makeRule({ intentPattern: "price amazon" });
    store.upsert(rule);

    const hint = store.buildHint("tell me about dogs");
    expect(hint).toBe("");
  });

  it("buildHint returns a hint string when a rule's intentPattern keywords match the user intent", () => {
    const store = new RoutingRuleStore(tmpDir);
    const rule = makeRule({ intentPattern: "price amazon", suggestedAlternatives: ["web_fetch", "live_browser"] });
    store.upsert(rule);

    const hint = store.buildHint("what is the price on amazon for headphones?");
    expect(hint).toContain("web_search");
    expect(hint).toContain("web_fetch");
    expect(hint).toContain("repeated failures");
    expect(hint.startsWith("⚡ Learned routing hints")).toBe(true);
  });

  it("persists across reload: rule is present after creating a new store from same path", () => {
    const store1 = new RoutingRuleStore(tmpDir);
    const rule = makeRule({ id: "persistent-rule" });
    store1.upsert(rule);

    // Create a fresh store pointing to the same path
    const store2 = new RoutingRuleStore(tmpDir);
    const retrieved = store2.getById("persistent-rule");
    expect(retrieved).toBeDefined();
    expect(retrieved!.failingTool).toBe("web_search");
    expect(retrieved!.intentPattern).toBe("price amazon");
  });
});
