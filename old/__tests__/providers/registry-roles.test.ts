import { describe, it, expect, beforeEach, vi } from "vitest";
import { ProviderRegistry } from "../../src/providers/registry.js";
import type { ModelProvider } from "../../src/providers/base.js";

// Suppress log noise during tests
vi.mock("../../src/logger.js", () => ({
  log: {
    engine: {
      warn: vi.fn(),
      debug: vi.fn(),
      info: vi.fn(),
      error: vi.fn(),
    },
  },
}));

function mockProvider(name: string): ModelProvider {
  return {
    name,
    healthCheck: async () => true,
    complete: async () => ({ content: "", usage: { inputTokens: 0, outputTokens: 0 } }),
  } as any;
}

describe("ProviderRegistry — role-based resolution", () => {
  let registry: ProviderRegistry;

  beforeEach(() => {
    registry = new ProviderRegistry();
  });

  it("byRole returns the provider after assignRole", () => {
    const provider = mockProvider("anthropic");
    registry._registerForTest("anthropic", provider);
    registry.assignRole("semantic-disambiguator", "anthropic");

    const result = registry.byRole("semantic-disambiguator");
    expect(result).toBe(provider);
  });

  it("byRole falls back to default when role not assigned", () => {
    const defaultProvider = mockProvider("default-provider");
    registry._registerForTest("default-provider", defaultProvider);
    registry.setDefault("default-provider");

    const result = registry.byRole("vision");
    expect(result).toBe(defaultProvider);
  });

  it("assignRole to unregistered provider does not crash and does not set the role", () => {
    // Should not throw
    expect(() => {
      registry.assignRole("synthesizer", "nonexistent-provider");
    }).not.toThrow();

    // Role should not be set — byRole should fall back to default
    const defaultProvider = mockProvider("default-provider");
    registry._registerForTest("default-provider", defaultProvider);
    registry.setDefault("default-provider");

    const result = registry.byRole("synthesizer");
    expect(result).toBe(defaultProvider);
  });

  it("autoAssignRoles with type 'anthropic' assigns semantic-disambiguator, synthesizer, tool-judge", () => {
    const anthropicProvider = mockProvider("my-anthropic");
    registry._registerForTest("my-anthropic", anthropicProvider);

    registry.autoAssignRoles([{ name: "my-anthropic", type: "anthropic" }]);

    expect(registry.byRole("semantic-disambiguator")).toBe(anthropicProvider);
    expect(registry.byRole("synthesizer")).toBe(anthropicProvider);
    expect(registry.byRole("tool-judge")).toBe(anthropicProvider);
  });

  it("autoAssignRoles does NOT overwrite explicitly assigned roles", () => {
    const explicitProvider = mockProvider("explicit-provider");
    const autoProvider = mockProvider("auto-provider");

    registry._registerForTest("explicit-provider", explicitProvider);
    registry._registerForTest("auto-provider", autoProvider);

    // Explicitly assign "synthesizer" before autoAssign
    registry.assignRole("synthesizer", "explicit-provider");

    // autoAssign should leave "synthesizer" alone
    registry.autoAssignRoles([{ name: "auto-provider", type: "anthropic" }]);

    expect(registry.byRole("synthesizer")).toBe(explicitProvider);
    // Other roles should still be auto-assigned
    expect(registry.byRole("semantic-disambiguator")).toBe(autoProvider);
    expect(registry.byRole("tool-judge")).toBe(autoProvider);
  });
});
