import { describe, it, expect, vi } from "vitest";

// ─── recordOutcome tests ──────────────────────────────────────────

describe("PelletStore.recordOutcome", () => {
  it("ADVANCES increments successCount", async () => {
    const mockGet = vi.fn().mockResolvedValue({ id: "p1", successCount: 2, failureCount: 0, provenance: [] });
    const mockLanceUpdate = vi.fn().mockResolvedValue(undefined);
    // Construct minimal PelletStore with mocked lance
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws");
    store._initialized = true;
    store.lance = { get: mockGet, updateCounters: mockLanceUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p1"], "ADVANCES");
    expect(mockLanceUpdate).toHaveBeenCalledWith("p1", 1, 0);
  });

  it("PARTIAL increments successCount", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws2");
    store._initialized = true;
    const mockUpdate = vi.fn().mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "p2", successCount: 0, failureCount: 0 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p2"], "PARTIAL");
    expect(mockUpdate).toHaveBeenCalledWith("p2", 1, 0);
  });

  it("BLOCKED increments failureCount", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws3");
    store._initialized = true;
    const mockUpdate = vi.fn().mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "p3", successCount: 0, failureCount: 1 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p3"], "BLOCKED");
    expect(mockUpdate).toHaveBeenCalledWith("p3", 0, 1);
  });

  it("NEUTRAL does not call updateCounters", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws4");
    store._initialized = true;
    const mockUpdate = vi.fn();
    store.lance = { updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p4"], "NEUTRAL");
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("multi-ID: one failure does not abort others", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws5");
    store._initialized = true;
    const mockUpdate = vi.fn()
      .mockRejectedValueOnce(new Error("db error"))
      .mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "x", successCount: 0, failureCount: 0 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await expect(store.recordOutcome(["bad", "good"], "ADVANCES")).resolves.not.toThrow();
    expect(mockUpdate).toHaveBeenCalledTimes(2);
  });
});

// ─── searchWithGraphScored quality re-rank test ──────────────────

describe("PelletStore.searchWithGraphScored — quality re-rank", () => {
  it("re-ranks by combined vector + quality score: high success rises above high vector-only", async () => {
    const pelletA = { id: "A", title: "A", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    const pelletB = { id: "B", title: "B", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 10, failureCount: 0, provenance: [] };
    const pelletC = { id: "C", title: "C", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 5, provenance: [] };

    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-rank");
    store._initialized = true;
    // searchWithGraph returns Pellet[] already sorted by vector score: A(0.9), C(0.8), B(0.7)
    store.searchWithGraph = vi.fn().mockResolvedValue([pelletA, pelletC, pelletB]);

    const result = await store.searchWithGraphScored("query", 5);
    // After quality re-rank:
    //   A: 0.9*0.8 + (0/(0+0+1))*0.2   = 0.72 + 0.0  = 0.720
    //   C: 0.8*0.8 + (0/(0+5+1))*0.2   = 0.64 + 0.0  = 0.640
    //   B: 0.7*0.8 + (10/(10+0+1))*0.2 = 0.56 + 0.182= 0.742
    // Expected order: B > A > C
    expect(result.map((r: any) => r.p.id)).toEqual(["B", "A", "C"]);
  });
});
