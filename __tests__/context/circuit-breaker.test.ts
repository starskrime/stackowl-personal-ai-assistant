import { describe, it, expect, vi, beforeEach } from "vitest";
import { LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore } from "../../src/context/circuit-breaker.js";
import type { ContextBuildTrace } from "../../src/context/layer.js";

describe("LayerCircuitBreaker", () => {
  it("starts CLOSED", () => {
    const cb = new LayerCircuitBreaker();
    expect(cb.state).toBe("CLOSED");
    expect(cb.shouldBypass()).toBe(false);
  });

  it("trips OPEN after >40% error rate", () => {
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 12; i++) cb.recordFailure(); // 12/20 = 60%
    for (let i = 0; i < 8; i++) cb.recordSuccess(100);
    expect(cb.state).toBe("OPEN");
    expect(cb.shouldBypass()).toBe(true);
  });

  it("transitions to HALF_OPEN after cooldown", () => {
    vi.useFakeTimers();
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 20; i++) cb.recordFailure();
    expect(cb.state).toBe("OPEN");
    vi.advanceTimersByTime(61_000);
    expect(cb.state).toBe("HALF_OPEN");
    vi.useRealTimers();
  });

  it("closes from HALF_OPEN on success probe", () => {
    vi.useFakeTimers();
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 20; i++) cb.recordFailure();
    vi.advanceTimersByTime(61_000);
    expect(cb.state).toBe("HALF_OPEN");
    cb.recordSuccess(100);
    expect(cb.state).toBe("CLOSED");
    vi.useRealTimers();
  });
});

describe("LayerHealthMonitor", () => {
  it("returns same breaker for same name", () => {
    const m = new LayerHealthMonitor();
    expect(m.getBreaker("L1")).toBe(m.getBreaker("L1"));
  });

  it("shouldBypass delegates to breaker", () => {
    const m = new LayerHealthMonitor();
    for (let i = 0; i < 20; i++) m.getBreaker("L1").recordFailure();
    expect(m.shouldBypass("L1")).toBe(true);
    expect(m.shouldBypass("L2")).toBe(false);
  });
});

describe("ContextQualityScore", () => {
  it("returns 1.0 for perfect trace", () => {
    const qs = new ContextQualityScore();
    const trace: ContextBuildTrace = [
      { layerName: "L1", priority: 10, batchIndex: 0, fired: true, cacheHit: false, tokensUsed: 100, durationMs: 50 },
      { layerName: "L2", priority: 20, batchIndex: 0, fired: true, cacheHit: false, tokensUsed: 100, durationMs: 50 },
    ];
    const score = qs.compute(trace, 2);
    expect(score).toBeCloseTo(1.0, 1);
  });

  it("returns < 0.6 when most layers skipped", () => {
    const qs = new ContextQualityScore();
    const trace: ContextBuildTrace = Array.from({ length: 10 }, (_, i) => ({
      layerName: `L${i}`, priority: i * 10, batchIndex: 0,
      fired: i < 2, cacheHit: false, tokensUsed: i < 2 ? 50 : 0, durationMs: 10,
    }));
    const score = qs.compute(trace, 10);
    expect(score).toBeLessThan(0.6);
  });
});
