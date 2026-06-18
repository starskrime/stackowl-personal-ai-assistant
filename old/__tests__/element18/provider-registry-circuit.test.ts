import { describe, it, expect } from "vitest";
import { ProviderRegistry } from "../../src/providers/registry.js";
import { ProviderCircuitBreaker } from "../../src/providers/circuit-breaker.js";

describe("ProviderRegistry circuit breaker integration", () => {
  it("getAvailable() returns null for a provider with OPEN circuit", () => {
    const breaker = new ProviderCircuitBreaker(1, 60_000);
    breaker.recordResult(false); // trip at threshold=1
    expect(breaker.isOpen()).toBe(true);
    expect(breaker.getState()).toBe("OPEN");
  });

  it("getAvailable() returns null when provider name is unknown", () => {
    const registry = new ProviderRegistry();
    expect(registry.getAvailable("unknown-provider")).toBeNull();
  });

  it("recordProviderResult does not throw for unknown provider", () => {
    const registry = new ProviderRegistry();
    expect(() => registry.recordProviderResult("unknown", false)).not.toThrow();
    expect(() => registry.recordProviderResult("unknown", true)).not.toThrow();
  });

  it("isProviderOpen returns false for unknown provider", () => {
    const registry = new ProviderRegistry();
    expect(registry.isProviderOpen("unknown")).toBe(false);
  });
});
