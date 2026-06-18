// __tests__/routing/owl-brain-soft-pin.test.ts
import { describe, it, expect } from "vitest"
import { OwlBrain } from "../../src/routing/owl-brain.js"

function makeDb() {
  const pins = new Map<string, string>()
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins: {
      get: (u: string, c: string) => pins.get(`${u}:${c}`) ?? null,
      set: (u: string, c: string, n: string | null, _ts?: string) => { if (n === null) pins.delete(`${u}:${c}`); else pins.set(`${u}:${c}`, n) },
    },
    _pins: pins,
  }
}

function makeMsg(text = "hello") {
  return { id: "m", channelId: "cli", userId: "u1", sessionId: "s1", text, timestamp: Date.now(), attachments: [] }
}

describe("OwlBrain soft-pin TTL", () => {
  it("signal routing match does NOT write to SQLite", async () => {
    const db = makeDb()
    const registry = {
      get: (n: string) => n === "aria" ? { name: "aria", type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: ["cook"], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
      listSpecialists: () => [{ name: "aria" }],
      getDefault: () => undefined,
    }
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    const mockRouter = {
      routeWithSignals: async () => ({ type: "specialist" as const, owl: registry.get("aria"), reason: "signal match" }),
    }
    brain.setSecretaryRouterGetter(() => mockRouter as any)

    const session = { metadata: {} } as any
    await brain.resolve("I love cooking", makeMsg("I love cooking"), {} as any, {} as any, session)

    // Session gets the soft pin
    expect(session.metadata.activeOwlName).toBe("aria")
    // SQLite pins do NOT get written
    expect((db as any)._pins.size).toBe(0)
  })

  it("3 consecutive non-matching turns clear the session soft pin", async () => {
    const db = makeDb()
    const registry = {
      get: (n: string) => n === "aria" ? { name: "aria", type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
      listSpecialists: () => [{ name: "aria" }],
      getDefault: () => undefined,
    }
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    const mockRouter = {
      routeWithSignals: async () => ({ type: "direct" as const, reason: "no match" }),
    }
    brain.setSecretaryRouterGetter(() => mockRouter as any)

    const session: any = { metadata: { activeOwlName: "aria", softPinMissCount: 0 } }

    // 2 misses — still pinned
    for (let i = 0; i < 2; i++) {
      await brain.resolve("hello world", makeMsg("hello world"), {} as any, {} as any, session)
    }
    expect(session.metadata.activeOwlName).toBe("aria")

    // 3rd miss — clears pin
    await brain.resolve("hello world", makeMsg("hello world"), {} as any, {} as any, session)
    expect(session.metadata.activeOwlName).toBeUndefined()
  })
})
