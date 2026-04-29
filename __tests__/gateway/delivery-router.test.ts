import { describe, it, expect, vi, beforeEach } from "vitest"
import { DeliveryRouter } from "../../src/gateway/delivery-router.js"
import { GatewayEventBus } from "../../src/gateway/event-bus.js"
import { ChannelRegistry } from "../../src/gateway/channel-registry.js"
import { makeEnvelope } from "../../src/gateway/delivery-envelope.js"
import type { ChannelAdapterV2 } from "../../src/gateway/adapter-v2.js"
import type { ChannelCapabilities } from "../../src/gateway/channel-capabilities.js"

function makeAdapter(id: string, caps: Partial<ChannelCapabilities> = {}): ChannelAdapterV2 {
  const defaults: ChannelCapabilities = {
    channelId: id, displayName: id,
    streaming: false, async: true, multiUser: false,
    maxMessageLength: 4096, formatting: "plain",
    supportsButtons: false, supportsFiles: false, supportsVoice: false,
    supportsImages: false, supportsThreads: false, supportsReactions: false,
    supportsInterrupt: false,
  }
  return {
    capabilities: { ...defaults, ...caps },
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    register: vi.fn(),
    deliver: vi.fn().mockResolvedValue(undefined),
    ask: vi.fn().mockResolvedValue("yes"),
  }
}

describe("DeliveryRouter", () => {
  let bus: GatewayEventBus
  let registry: ChannelRegistry
  // pass retryDelaysMs=[0,0,0] so retries are instant in tests
  let router: DeliveryRouter

  beforeEach(() => {
    bus = new GatewayEventBus()
    registry = new ChannelRegistry()
    router = new DeliveryRouter(registry, undefined, [0, 0, 0])
    router.start(bus)
  })

  it("delivers envelope to the correct adapter via channelId", async () => {
    const adapter = makeAdapter("telegram")
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    const envelope = makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "hi", streamable: false },
      urgency: "normal", trigger: "user-request",
    })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    expect(adapter.deliver).toHaveBeenCalledWith(envelope)
  })

  it("drops envelope when TTL has expired before routing", async () => {
    const adapter = makeAdapter("telegram")
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    const envelope = makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "stale", streamable: false },
      urgency: "proactive", trigger: "proactive",
      ttlMs: 1,
    })
    // backdate createdAt so TTL is already expired
    Object.assign(envelope, { createdAt: Date.now() - 10_000 })
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    expect(adapter.deliver).not.toHaveBeenCalled()
  })

  it("drops silently when no channel is available — does not throw", async () => {
    // no adapter registered
    const envelope = makeEnvelope({
      userId: "u1",
      content: { text: "hello", streamable: false },
      urgency: "normal", trigger: "user-request",
    })
    // Should not throw even though there is no channel
    bus.publish(envelope)
    await new Promise(r => setTimeout(r, 20))
    // If we get here without an unhandled rejection, the test passes
    expect(true).toBe(true)
  })

  it("retries delivery up to MAX_RETRIES on transient failure", async () => {
    const adapter = makeAdapter("telegram")
    adapter.deliver = vi.fn().mockRejectedValue(new Error("timeout"))
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    bus.publish(makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "retry me", streamable: false },
      urgency: "normal", trigger: "user-request",
    }))
    await new Promise(r => setTimeout(r, 50))
    // 1 initial + 2 retries = 3 total
    expect(adapter.deliver).toHaveBeenCalledTimes(3)
  })

  it("stops retrying after a successful delivery", async () => {
    const adapter = makeAdapter("telegram")
    let calls = 0
    adapter.deliver = vi.fn().mockImplementation(async () => {
      calls++
      if (calls === 1) throw new Error("first fail")
      // second call succeeds
    })
    registry.register(adapter)
    registry.markActive("telegram", "u1")

    bus.publish(makeEnvelope({
      userId: "u1", channelId: "telegram",
      content: { text: "retry once", streamable: false },
      urgency: "normal", trigger: "user-request",
    }))
    await new Promise(r => setTimeout(r, 50))
    expect(calls).toBe(2)  // failed once, succeeded on retry
  })
})
