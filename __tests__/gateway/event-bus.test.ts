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
