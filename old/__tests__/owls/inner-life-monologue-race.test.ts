// __tests__/owls/inner-life-monologue-race.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { OwlInnerLife } from "../../src/owls/inner-life.js"
import type { ModelProvider } from "../../src/providers/base.js"
import type { OwlInstance } from "../../src/owls/persona.js"

function makeMinimalOwl(): OwlInstance {
  return {
    persona: {
      name: "TestOwl",
      type: "test",
      emoji: "🦉",
      challengeLevel: "medium",
      specialties: ["general"],
      traits: ["thoughtful"],
      systemPrompt: "You are TestOwl.",
      sourcePath: "/test/OWL.md",
    },
    dna: {
      version: 1,
      owlName: "TestOwl",
      challengeLevel: "medium",
      verbosity: "balanced",
      learnedPreferences: {},
      expertiseGrowth: {},
      interactionStats: {
        totalConversations: 0,
        adviceAcceptedRate: 0,
        topicsDiscussed: [],
        lastActiveAt: new Date().toISOString(),
      },
      evolvedTraits: {
        challengeLevel: 0.5,
        verbosity: 0.5,
        humor: 0.5,
        formality: 0.5,
        proactivity: 0.5,
        riskTolerance: 0.5,
        teachingStyle: 0.5,
        delegationPreference: 0.5,
      },
      promptSections: [],
    },
  } as unknown as OwlInstance
}

describe("monologue race fix", () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it("thinkInBackground returns a Promise (not void)", async () => {
    const provider: ModelProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({ content: "{}", model: "m", finishReason: "stop" }),
      listModels: vi.fn().mockResolvedValue([]),
    } as unknown as ModelProvider
    const life = new OwlInnerLife(provider, makeMinimalOwl(), "/tmp/test-workspace")
    const result = life.thinkInBackground("hello", [])
    expect(result).toBeInstanceOf(Promise)
    await result
  })

  it("monologue is written before Promise resolves", async () => {
    const provider: ModelProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify({
          thoughts: ["thinking"], responseIntent: "assist", moodShift: { current: "curious" },
        }),
        model: "m", finishReason: "stop",
      }),
      listModels: vi.fn().mockResolvedValue([]),
    } as unknown as ModelProvider

    const life = new OwlInnerLife(provider, makeMinimalOwl(), "/tmp/test-workspace")

    await life.thinkInBackground("test message", [])
    const monologue = life.getLastMonologue()
    expect(monologue).not.toBeNull()
  })
})
