import { describe, it, expect, vi } from "vitest"
import { GatewayEventBus } from "../../src/gateway/event-bus.js"
import { makeEnvelope } from "../../src/gateway/delivery-envelope.js"

describe("GatewayEventBus", () => {
  it("routes published envelopes to onDeliver handler", async () => {
    const bus = new GatewayEventBus()
    const received: unknown[] = []
    bus.onDeliver(async env => { received.push(env) })

    const envelope = makeEnvelope({
      userId: "u1",
      content: { text: "hello", streamable: false },
      urgency: "normal",
      trigger: "user-request",
    })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 0))
    expect(received).toHaveLength(1)
    expect((received[0] as typeof envelope).userId).toBe("u1")
  })

  it("routes system events to typed handlers", () => {
    const bus = new GatewayEventBus()
    const received: unknown[] = []
    bus.on("parliament:done", e => { received.push(e) })
    bus.emit({ type: "parliament:done", topic: "AI safety", verdict: "inconclusive", userId: "u1" })
    expect(received).toHaveLength(1)
    expect((received[0] as any).topic).toBe("AI safety")
  })

  it("delivery events do not reach system event handlers", () => {
    const bus = new GatewayEventBus()
    const systemReceived: unknown[] = []
    bus.on("cost:alert", e => systemReceived.push(e))
    bus.publish(makeEnvelope({
      userId: "u1",
      content: { text: "hi", streamable: false },
      urgency: "normal",
      trigger: "user-request",
    }))
    expect(systemReceived).toHaveLength(0)
  })

  it("multiple onDeliver handlers all receive the envelope", async () => {
    const bus = new GatewayEventBus()
    const calls: number[] = []
    bus.onDeliver(async () => { calls.push(1) })
    bus.onDeliver(async () => { calls.push(2) })
    bus.publish(makeEnvelope({ userId: "u1", content: { text: "x", streamable: false }, urgency: "normal", trigger: "proactive" }))
    await new Promise(r => setTimeout(r, 0))
    expect(calls.sort()).toEqual([1, 2])
  })
})

describe("GatewayEventBus tool events", () => {
  it("emits and receives tool:start event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:start", handler);
    bus.emit({ type: "tool:start", toolName: "web_crawl", args: { url: "https://x.com" }, turnId: "t1" });
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ type: "tool:start", toolName: "web_crawl" })
    );
  });

  it("emits and receives tool:result event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:result", handler);
    bus.emit({ type: "tool:result", toolName: "web_crawl", success: true, durationMs: 120, truncated: false });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ success: true, durationMs: 120 }));
  });

  it("emits and receives tool:goal_blocked event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("tool:goal_blocked", handler);
    bus.emit({ type: "tool:goal_blocked", toolName: "web_search", subGoal: "find price data", suggestion: "try web_crawl with specific URL" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ suggestion: "try web_crawl with specific URL" }));
  });
})
