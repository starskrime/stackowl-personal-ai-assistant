import { describe, it, expect } from "vitest";
import { GatewayEventBus, type GatewaySystemEvent } from "../src/gateway/event-bus.js";

const VARIANTS: GatewaySystemEvent["type"][] = [
  "memory:written",
  "memory:invalidated",
  "memory:classify_failed",
  "memory:contradict_failed",
  "memory:write_failed",
  "memory:contradiction_detected",
  "memory:accessed",
  "memory:render_failed",
  "memory:invalidate_rejected",
  "memory:slo_breach",
  "memory:health_degraded",
];

describe("GatewayEventBus — memory:* variants", () => {
  it("all 11 memory:* variants compile and are subscribable", () => {
    const bus = new GatewayEventBus();
    let count = 0;
    for (const v of VARIANTS) {
      bus.on(v, () => {
        count += 1;
      });
    }
    expect(VARIANTS).toHaveLength(11);
    expect(count).toBe(0);
  });

  it("memory:written round-trips with full payload", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:written", (e) => captured.push(e));
    bus.emit({
      type: "memory:written",
      id: "x",
      kind: "semantic",
      goal_id: null,
      importance: 0.5,
    });
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ id: "x", kind: "semantic", importance: 0.5 });
  });

  it("memory:invalidated round-trips with full payload", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:invalidated", (e) => captured.push(e));
    bus.emit({
      type: "memory:invalidated",
      id: "x",
      reason: "manual",
      invalidated_by: "test",
    });
    expect(captured).toHaveLength(1);
    expect(captured[0]).toMatchObject({ reason: "manual", invalidated_by: "test" });
  });

  it("memory:classify_failed round-trips with turnId+reason", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:classify_failed", (e) => captured.push(e));
    bus.emit({ type: "memory:classify_failed", turnId: "t1", reason: "provider down" });
    expect(captured[0]).toMatchObject({ turnId: "t1", reason: "provider down" });
  });

  it("memory:contradict_failed round-trips", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:contradict_failed", (e) => captured.push(e));
    bus.emit({ type: "memory:contradict_failed", reason: "bad json" });
    expect(captured[0]).toMatchObject({ reason: "bad json" });
  });

  it("memory:write_failed round-trips", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:write_failed", (e) => captured.push(e));
    bus.emit({ type: "memory:write_failed", reason: "disk full" });
    expect(captured[0]).toMatchObject({ reason: "disk full" });
  });

  it("memory:contradiction_detected round-trips with both ids", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:contradiction_detected", (e) => captured.push(e));
    bus.emit({
      type: "memory:contradiction_detected",
      memoryId: "m1",
      contradictsId: "m2",
      reason: "user updated preference",
    });
    expect(captured[0]).toMatchObject({ memoryId: "m1", contradictsId: "m2" });
  });

  it("memory:accessed round-trips", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:accessed", (e) => captured.push(e));
    bus.emit({ type: "memory:accessed", id: "m1", kind: "semantic" });
    expect(captured[0]).toMatchObject({ id: "m1", kind: "semantic" });
  });

  it("memory:render_failed round-trips with layerName", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:render_failed", (e) => captured.push(e));
    bus.emit({
      type: "memory:render_failed",
      layerName: "memory.semantic",
      reason: "timeout",
    });
    expect(captured[0]).toMatchObject({ layerName: "memory.semantic", reason: "timeout" });
  });

  it("memory:invalidate_rejected round-trips", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:invalidate_rejected", (e) => captured.push(e));
    bus.emit({ type: "memory:invalidate_rejected", id: "m1", reason: "user denied" });
    expect(captured[0]).toMatchObject({ id: "m1", reason: "user denied" });
  });

  it("memory:slo_breach round-trips with metric+observed+budget", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:slo_breach", (e) => captured.push(e));
    bus.emit({ type: "memory:slo_breach", metric: "search_p95", observed: 250, budget: 100 });
    expect(captured[0]).toMatchObject({ metric: "search_p95", observed: 250, budget: 100 });
  });

  it("memory:health_degraded round-trips", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("memory:health_degraded", (e) => captured.push(e));
    bus.emit({ type: "memory:health_degraded", reason: "high error rate" });
    expect(captured[0]).toMatchObject({ reason: "high error rate" });
  });

  it("engine:turn_complete round-trips with sessionId", () => {
    const bus = new GatewayEventBus();
    const captured: GatewaySystemEvent[] = [];
    bus.on("engine:turn_complete", (e) => captured.push(e));
    bus.emit({ type: "engine:turn_complete", sessionId: "s1" });
    expect(captured[0]).toMatchObject({ sessionId: "s1" });
  });
});
