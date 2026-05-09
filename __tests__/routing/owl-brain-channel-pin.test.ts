// __tests__/routing/owl-brain-channel-pin.test.ts
import { describe, it, expect } from "vitest"
import { OwlBrain } from "../../src/routing/owl-brain.js"
import type { GatewayMessage } from "../../src/gateway/types.js"

function makeDb(pins: Map<string, string> = new Map()) {
  const owlPins = {
    get: (userId: string, channelId: string) => {
      return pins.get(`${userId}:${channelId}`) ?? pins.get(`${userId}:global`) ?? null
    },
    set: (userId: string, channelId: string, owlName: string | null, _pinnedAt: string) => {
      if (owlName === null) pins.delete(`${userId}:${channelId}`)
      else pins.set(`${userId}:${channelId}`, owlName)
    },
  }
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins,
  }
}

function makeMsg(channelId: string, text = "hello"): GatewayMessage {
  return {
    id: "m1", channelId, userId: "user1", sessionId: `${channelId}:user1`,
    text, timestamp: Date.now(), attachments: [],
  }
}

function makeRegistry(owls: string[]) {
  const makeSpec = (name: string) => ({
    name, role: "test", emoji: "🤖", type: "specialist",
    personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" },
    expertise: [], model: { provider: "anthropic", modelId: "m" },
    permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
    routingRules: { keywords: [], domains: [], priority: 5 },
    skills: { canLearn: false, retainedKnowledge: [] },
    additionalPrompt: "",
  })
  return {
    get: (name: string) => owls.includes(name) ? makeSpec(name) : undefined,
    listSpecialists: () => owls.map(name => ({ name } as any)),
    getDefault: () => undefined,
  }
}

describe("OwlBrain per-channel pin isolation", () => {
  it("telegram pin does not bleed to CLI", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)

    // Pin aria on telegram via @mention
    await brain.resolve("@aria hello", makeMsg("telegram"), {} as any, {} as any, { metadata: {} } as any)
    // CLI should not see the pin
    const cli = await brain.resolve("hello", makeMsg("cli"), {} as any, {} as any, { metadata: {} } as any)
    expect(cli.activeOwlName).toBe("noctua")
  })

  it("pin set on @mention is per-channel", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)

    await brain.resolve("@aria hello", makeMsg("telegram"), {} as any, {} as any, { metadata: {} } as any)
    expect(pins.get("user1:telegram")).toBe("aria")
    expect(pins.get("user1:cli")).toBeUndefined()
  })
})
