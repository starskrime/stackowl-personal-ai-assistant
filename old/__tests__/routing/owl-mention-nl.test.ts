// __tests__/routing/owl-mention-nl.test.ts
import { describe, it, expect, vi } from "vitest"
import { OwlBrain } from "../../src/routing/owl-brain.js"

function makeDb(pins = new Map<string, string>()) {
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins: {
      get: (u: string, c: string) => pins.get(`${u}:${c}`) ?? null,
      set: (u: string, c: string, n: string | null, _ts?: string) => { if (n === null) pins.delete(`${u}:${c}`); else pins.set(`${u}:${c}`, n) },
    },
    _pins: pins,
  }
}

function makeRegistry(names: string[]) {
  return {
    get: (n: string) => names.includes(n) ? { name: n, type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
    listSpecialists: () => names.map(n => ({ name: n })),
    getDefault: () => undefined,
  }
}

function makeMsg(text: string) {
  return { id: "m", channelId: "cli", userId: "u1", sessionId: "s1", text, timestamp: Date.now(), attachments: [] }
}

describe("NL mention parser", () => {
  it("high-confidence mention hard-pins and routes to named helper", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(async (_prompt) =>
      JSON.stringify({ targeted: "aria", confidence: 0.9 })
    )

    const session: any = { metadata: {} }
    const result = await brain.resolve("Aria, can you check the weather?", makeMsg("Aria, can you check the weather?"), {} as any, {} as any, session)

    expect(result.activeOwlName).toBe("aria")
    expect(pins.get("u1:cli")).toBe("aria") // hard pin written
  })

  it("low-confidence does not route to named helper (silent fallback)", async () => {
    const db = makeDb()
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(async (_prompt) =>
      JSON.stringify({ targeted: "aria", confidence: 0.4 })
    )

    const session: any = { metadata: {} }
    const result = await brain.resolve("Aria said it was raining yesterday", makeMsg("Aria said it was raining yesterday"), {} as any, {} as any, session)

    expect(result.activeOwlName).toBe("noctua") // silent fallback
  })

  it("no classification when roster is empty", async () => {
    const classifyFn = vi.fn().mockResolvedValue(JSON.stringify({ targeted: null, confidence: 0 }))
    const db = makeDb()
    const registry = makeRegistry([]) // no helpers
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(classifyFn)

    await brain.resolve("Aria, hello", makeMsg("Aria, hello"), {} as any, {} as any, { metadata: {} } as any)
    expect(classifyFn).not.toHaveBeenCalled()
  })
})
