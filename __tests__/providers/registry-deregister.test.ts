import { describe, it, expect, vi } from "vitest";
import { ProviderRegistry } from "../../src/providers/registry.js";
import type { ModelProvider } from "../../src/providers/base.js";

function makeProvider(): ModelProvider {
  return {
    name: "test",
    chat: vi.fn(),
    stream: vi.fn(),
    healthCheck: vi.fn().mockResolvedValue(true),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider;
}

describe("ProviderRegistry.deregister", () => {
  it("removes the provider so get() throws after deregistration", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-a", makeProvider());
    expect(() => reg.get("prov-a")).not.toThrow();

    reg.deregister("prov-a");

    expect(() => reg.get("prov-a")).toThrow(/prov-a.*not found/i);
  });

  it("removes circuit breaker entry", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-b", makeProvider());
    reg.deregister("prov-b");

    // isProviderOpen returns false (no breaker) — not true
    expect(reg.isProviderOpen("prov-b")).toBe(false);
  });

  it("clears role assignments for the deregistered provider", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-c", makeProvider());
    reg.assignRole("synthesizer", "prov-c");
    reg.deregister("prov-c");

    // byRole falls back to default — with no default set, it throws
    expect(() => reg.byRole("synthesizer")).toThrow();
  });

  it("clears defaultProviderName when deregistering the default", () => {
    const reg = new ProviderRegistry();
    reg._registerForTest("prov-d", makeProvider());
    reg._registerForTest("prov-e", makeProvider());
    // Manually set default (setDefault requires registry.register, bypass via context)
    (reg as any).defaultProviderName = "prov-d";
    reg.deregister("prov-d");

    expect(reg.getDefaultName()).toBeNull();
  });

  it("is a no-op for unknown provider names", () => {
    const reg = new ProviderRegistry();
    expect(() => reg.deregister("does-not-exist")).not.toThrow();
  });
});
