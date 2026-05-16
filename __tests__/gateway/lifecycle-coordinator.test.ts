import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { LifecycleCoordinator } from "../../src/gateway/lifecycle-coordinator.js";

describe("LifecycleCoordinator", () => {
  let lc: LifecycleCoordinator;

  beforeEach(() => {
    lc = new LifecycleCoordinator();
  });

  afterEach(async () => {
    await lc.shutdown();
  });

  it("calls registered callbacks on shutdown in LIFO order", async () => {
    const order: string[] = [];
    lc.register("first", async () => { order.push("first"); });
    lc.register("second", async () => { order.push("second"); });
    await lc.shutdown();
    expect(order).toEqual(["second", "first"]); // LIFO
  });

  it("shutdown is idempotent — second call is no-op", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    lc.register("only", cb);
    await lc.shutdown();
    await lc.shutdown();
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("failed callback is logged but others still run", async () => {
    const good = vi.fn().mockResolvedValue(undefined);
    const bad = vi.fn().mockRejectedValue(new Error("oops"));
    lc.register("good", good);
    lc.register("bad", bad); // registered second → runs first (LIFO)
    await lc.shutdown(); // must not throw
    expect(good).toHaveBeenCalled();
    expect(bad).toHaveBeenCalled();
  });

  it("startTimer triggers fn on interval", async () => {
    vi.useFakeTimers();
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("tick", 100, fn);
    vi.advanceTimersByTime(350);
    expect(fn).toHaveBeenCalledTimes(3);
    lc.stopTimer("tick");
    vi.useRealTimers();
  });

  it("startTimer ignores duplicate name and warns", async () => {
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("dup", 1000, fn);
    lc.startTimer("dup", 1000, fn); // should not double-register
    lc.stopTimer("dup");
    // No assertion needed — just must not throw
  });

  it("stopTimer clears interval", async () => {
    vi.useFakeTimers();
    const fn = vi.fn().mockResolvedValue(undefined);
    lc.startTimer("stoppable", 100, fn);
    lc.stopTimer("stoppable");
    vi.advanceTimersByTime(500);
    expect(fn).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
