import { describe, it, expect, vi, beforeEach } from "vitest"
import { ChannelRegistry } from "../../src/gateway/channel-registry.js"
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

describe("ChannelRegistry", () => {
  let registry: ChannelRegistry
  beforeEach(() => { registry = new ChannelRegistry() })

  it("registers and retrieves an adapter by channelId", () => {
    const a = makeAdapter("telegram")
    registry.register(a)
    expect(registry.get("telegram")).toBe(a)
  })

  it("unregisters an adapter and clears its presence data", () => {
    registry.register(makeAdapter("telegram"))
    registry.markActive("telegram", "user1")
    registry.unregister("telegram")
    expect(registry.get("telegram")).toBeUndefined()
    expect(registry.getLastSeen("telegram", "user1")).toBe(0)
  })

  it("listAll returns all registered adapters", () => {
    registry.register(makeAdapter("telegram"))
    registry.register(makeAdapter("slack"))
    expect(registry.listAll()).toHaveLength(2)
  })

  it("markActive makes channel appear in getActiveChannels", () => {
    const t = makeAdapter("telegram")
    const s = makeAdapter("slack")
    registry.register(t)
    registry.register(s)
    registry.markActive("telegram", "user1")
    const active = registry.getActiveChannels("user1")
    expect(active).toContain(t)
    expect(active).not.toContain(s)
  })

  it("getBestChannel — interrupt picks first supportsInterrupt adapter regardless of presence", () => {
    const cli = makeAdapter("cli", { async: false, supportsInterrupt: false })
    const tg = makeAdapter("telegram", { async: true, supportsInterrupt: true })
    registry.register(cli)
    registry.register(tg)
    // no markActive — user not currently active anywhere
    expect(registry.getBestChannel("user1", "interrupt")).toBe(tg)
  })

  it("getBestChannel — proactive skips non-async adapters", () => {
    const cli = makeAdapter("cli", { async: false })
    const tg = makeAdapter("telegram", { async: true })
    registry.register(cli)
    registry.register(tg)
    registry.markActive("cli", "user1")
    registry.markActive("telegram", "user1")
    expect(registry.getBestChannel("user1", "proactive")).toBe(tg)
  })

  it("getBestChannel — normal returns most-recently-active channel", async () => {
    const tg = makeAdapter("telegram")
    const sl = makeAdapter("slack")
    registry.register(tg)
    registry.register(sl)
    registry.markActive("telegram", "user1")
    await new Promise(r => setTimeout(r, 2))  // ensure different timestamps
    registry.markActive("slack", "user1")
    expect(registry.getBestChannel("user1", "normal")).toBe(sl)
  })

  it("getBestChannel — returns undefined when no active channels for normal urgency", () => {
    registry.register(makeAdapter("telegram"))
    expect(registry.getBestChannel("user1", "normal")).toBeUndefined()
  })
})
