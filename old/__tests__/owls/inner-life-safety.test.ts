// __tests__/owls/inner-life-safety.test.ts
import { describe, it, expect, vi } from "vitest"
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

function makeMockProvider(): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: "{}", model: "m", finishReason: "stop" }),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider
}

describe("OwlInnerLife jailbreak surfaces removed", () => {
  it("toContextString does not exist", () => {
    const life = new OwlInnerLife(makeMockProvider(), makeMinimalOwl(), "/tmp/test-workspace")
    expect((life as any).toContextString).toBeUndefined()
  })

  it("monologueToDirective does not exist", () => {
    const life = new OwlInnerLife(makeMockProvider(), makeMinimalOwl(), "/tmp/test-workspace")
    expect((life as any).monologueToDirective).toBeUndefined()
  })

  it("thinkInBackground returns a Promise", async () => {
    const life = new OwlInnerLife(makeMockProvider(), makeMinimalOwl(), "/tmp/test-workspace")
    const result = life.thinkInBackground("hello", [])
    expect(result).toBeInstanceOf(Promise)
    await result
  })
})
