import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("GatewayEventBus signal events", () => {
  it("delivers signal:emitted to subscribers", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("signal:emitted", handler);
    bus.emit({
      type: "signal:emitted",
      signal: {
        id: "s",
        source: "git",
        priority: "low",
        title: "t",
        content: "c",
        timestamp: 0,
        ttlMs: 1000,
      },
    });
    expect(handler).toHaveBeenCalled();
  });

  it("delivers signal:promoted with goal + rationale + verdict", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("signal:promoted", handler);
    bus.emit({
      type: "signal:promoted",
      signal: {
        id: "s",
        source: "git",
        priority: "high",
        title: "t",
        content: "c",
        timestamp: 0,
        ttlMs: 1000,
      },
      goal: { id: "g", title: "Ship 16b" },
      rationale: "edits in scope",
      verdict: "ADVANCES",
    });
    expect(handler).toHaveBeenCalled();
    const arg = handler.mock.calls[0][0];
    expect(arg.goal.id).toBe("g");
    expect(arg.rationale).toBe("edits in scope");
  });

  it("delivers signal:expired, signal:suppressed, signal:consent_changed", () => {
    const bus = new GatewayEventBus();
    const expired = vi.fn();
    const suppressed = vi.fn();
    const consent = vi.fn();
    bus.on("signal:expired", expired);
    bus.on("signal:suppressed", suppressed);
    bus.on("signal:consent_changed", consent);
    bus.emit({
      type: "signal:expired",
      signal: {
        id: "s",
        source: "git",
        priority: "low",
        title: "t",
        content: "c",
        timestamp: 0,
        ttlMs: 1000,
      },
      reason: "ttl",
    });
    bus.emit({
      type: "signal:suppressed",
      signal: {
        id: "s",
        source: "git",
        priority: "high",
        title: "t",
        content: "c",
        timestamp: 0,
        ttlMs: 1000,
      },
      verdict: "NEUTRAL",
    });
    bus.emit({
      type: "signal:consent_changed",
      source: "clipboard",
      granted: true,
    });
    expect(expired).toHaveBeenCalled();
    expect(suppressed).toHaveBeenCalled();
    expect(consent).toHaveBeenCalled();
  });
});
