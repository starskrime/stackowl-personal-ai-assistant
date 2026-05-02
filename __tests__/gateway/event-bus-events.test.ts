// __tests__/gateway/event-bus-events.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("new GatewaySystemEvent types", () => {
  it("emits and receives task:failed event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("task:failed", handler);
    bus.emit({ type: "task:failed", userId: "u1", taskDescription: "test task", toolSequence: ["web"], errorSummary: "404", category: "research", complexityTier: "medium" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "task:failed", userId: "u1" }));
  });

  it("emits and receives fact:extracted event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("fact:extracted", handler);
    bus.emit({ type: "fact:extracted", userId: "u1", factText: "user likes TypeScript", factId: "f1" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "fact:extracted", factText: "user likes TypeScript" }));
  });

  it("emits and receives session:ended event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("session:ended", handler);
    bus.emit({ type: "session:ended", userId: "u1", sessionId: "s1" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "session:ended", sessionId: "s1" }));
  });
});
