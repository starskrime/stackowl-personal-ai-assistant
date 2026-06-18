import { describe, it, expect, beforeEach } from "vitest";
import { ProviderCircuitBreaker } from "../../src/providers/circuit-breaker.js";

describe("ProviderCircuitBreaker", () => {
  let breaker: ProviderCircuitBreaker;

  beforeEach(() => {
    breaker = new ProviderCircuitBreaker(3, 1000); // threshold=3, timeout=1s
  });

  it("starts CLOSED and allows requests", () => {
    expect(breaker.isOpen()).toBe(false);
    expect(breaker.getState()).toBe("CLOSED");
  });

  it("transitions CLOSED → OPEN after failureThreshold failures", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(false); // not yet at threshold
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(true);
    expect(breaker.getState()).toBe("OPEN");
  });

  it("transitions OPEN → HALF_OPEN after recoveryTimeoutMs", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // now OPEN
    expect(breaker.isOpen()).toBe(true);

    // Fast-forward the clock past recovery timeout
    breaker._forceOpenedAt(Date.now() - 1001);
    expect(breaker.isOpen()).toBe(false); // HALF_OPEN lets one through
    expect(breaker.getState()).toBe("HALF_OPEN");
  });

  it("HALF_OPEN probe success → CLOSED", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // OPEN
    breaker._forceOpenedAt(Date.now() - 1001);
    breaker.isOpen(); // transition to HALF_OPEN
    breaker.recordResult(true); // probe success
    expect(breaker.getState()).toBe("CLOSED");
    expect(breaker.isOpen()).toBe(false);
  });

  it("HALF_OPEN probe failure → OPEN (timer reset)", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // OPEN
    breaker._forceOpenedAt(Date.now() - 1001);
    breaker.isOpen(); // transition to HALF_OPEN
    breaker.recordResult(false); // probe failure
    expect(breaker.getState()).toBe("OPEN");
    expect(breaker.isOpen()).toBe(true); // timer reset — not yet expired
  });

  it("success resets failure counter and closes circuit", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(true); // success before threshold
    expect(breaker.getState()).toBe("CLOSED");
    // Need 3 more failures to open again
    breaker.recordResult(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(true);
  });
});
