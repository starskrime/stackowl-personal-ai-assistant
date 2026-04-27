import { describe, it, expect, beforeEach } from "vitest";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { join } from "node:path";

describe("SpecializedOwlRegistry", () => {
  const testWorkspace = join(__dirname, "test-workspace");

  let registry: SpecializedOwlRegistry;

  beforeEach(() => {
    registry = new SpecializedOwlRegistry();
  });

  it("should load specialized owls from workspace", async () => {
    await registry.loadAll(testWorkspace);
    const owl = registry.get("tradingbot");
    expect(owl).toBeDefined();
    expect(owl?.role).toBe("Stock trading assistant");
  });

  it("should return undefined for non-existent owl", async () => {
    await registry.loadAll(testWorkspace);
    const owl = registry.get("nonexistent");
    expect(owl).toBeUndefined();
  });

  it("should find owls by keyword", async () => {
    await registry.loadAll(testWorkspace);
    const owls = registry.getByKeyword("trading");
    expect(owls.length).toBeGreaterThan(0);
  });

  it("should find owls by expertise domain", async () => {
    await registry.loadAll(testWorkspace);
    const owls = registry.getByExpertise("stock market");
    expect(owls.length).toBeGreaterThan(0);
  });

  it("should list all owls", async () => {
    await registry.loadAll(testWorkspace);
    const owls = registry.listAll();
    expect(owls.length).toBeGreaterThan(0);
  });

  it("should set credentialsPath for each owl", async () => {
    await registry.loadAll(testWorkspace);
    const owl = registry.get("tradingbot");
    expect(owl?.credentialsPath).toBeDefined();
    expect(owl?.credentialsPath).toContain("TradingBot");
  });
});
