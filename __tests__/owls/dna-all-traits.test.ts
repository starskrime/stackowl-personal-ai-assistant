// __tests__/owls/dna-all-traits.test.ts
import { describe, it, expect } from "vitest"
import { createDefaultDNA } from "../../src/owls/persona.js"
import { OwlEngine } from "../../src/engine/runtime.js"
import { vi } from "vitest"
import type { ModelProvider } from "../../src/providers/base.js"
import type { OwlPersona } from "../../src/owls/persona.js"

function makeMockProvider(): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: "ok", model: "m", finishReason: "stop" }),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider
}

function makePersona(): OwlPersona {
  return {
    name: "TestOwl", type: "test", emoji: "🦉",
    challengeLevel: "medium", specialties: [], traits: [],
    systemPrompt: "You are TestOwl.", sourcePath: "/test/OWL.md",
  }
}

describe("DNA all-8-traits directives", () => {
  it("all 8 traits present in evolvedTraits shape", () => {
    const dna = createDefaultDNA("TestOwl", "medium")
    const traits = dna.evolvedTraits
    expect(traits).toHaveProperty("challengeLevel")
    expect(traits).toHaveProperty("verbosity")
    expect(traits).toHaveProperty("humor")
    expect(traits).toHaveProperty("formality")
    expect(traits).toHaveProperty("proactivity")
    expect(traits).toHaveProperty("riskTolerance")
    expect(traits).toHaveProperty("teachingStyle")
    expect(traits).toHaveProperty("delegationPreference")
  })

  it("provider receives system prompt containing all 6 new trait directives", async () => {
    const provider = makeMockProvider()
    const engine = new OwlEngine()
    const dna = createDefaultDNA("TestOwl", "medium")

    await engine.run("hello", {
      provider,
      owl: { persona: makePersona(), dna },
      sessionHistory: [],
      config: { defaultProvider: "mock", providers: {}, owlDna: {}, parliament: {}, heartbeat: {}, smartRouting: { enabled: false }, web: {} } as any,
    })

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0]
    const systemMsg = chatCall[0].find((m: any) => m.role === "system")
    const sys: string = systemMsg?.content ?? ""

    expect(sys).toContain("humor")
    expect(sys).toContain("formality")
    expect(sys).toContain("proactivity")
    expect(sys).toContain("riskTolerance")
    expect(sys).toContain("teachingStyle")
    expect(sys).toContain("delegationPreference")
  })
})
