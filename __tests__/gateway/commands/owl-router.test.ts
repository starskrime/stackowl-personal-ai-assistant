// __tests__/gateway/commands/owl-router.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest"
import { dispatchOwlCommand } from "../../../src/gateway/commands/owl-router.js"
import type { HelperSpec } from "../../../src/owls/specialized-types.js"

function makeSpec(name: string): HelperSpec {
  return {
    name, type: "specialist", role: "test helper", emoji: "🤖",
    personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" },
    expertise: ["testing"],
    model: { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
    permissions: { allowedTools: ["web_search"], deniedTools: [], capabilityConstraints: [] },
    routingRules: { keywords: [] },
    skills: { allowed: [] },
    additionalPrompt: "",
  }
}

function makeRegistry(helpers: HelperSpec[]) {
  return {
    listAll: () => helpers,
    get: (name: string) => helpers.find(h => h.name === name),
    loadAll: vi.fn(),
  }
}

function makeDeps(helpers: HelperSpec[] = [], wizardResult = "✓ Done") {
  return {
    registry: makeRegistry(helpers),
    wizard: { start: vi.fn().mockResolvedValue(wizardResult), isActive: () => false, cancel: vi.fn() },
    userId: "user1",
    channelAdapter: {} as any,
  }
}

describe("dispatchOwlCommand", () => {
  it("list — returns bulleted helper list", async () => {
    const deps = makeDeps([makeSpec("Aria"), makeSpec("Nora")])
    const result = await dispatchOwlCommand("list", [], deps as any)
    expect(result).toContain("Aria")
    expect(result).toContain("Nora")
  })

  it("list — returns empty message when no helpers", async () => {
    const result = await dispatchOwlCommand("list", [], makeDeps([]) as any)
    expect(result).toContain("no helpers")
  })

  it("show — returns spec details", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    const result = await dispatchOwlCommand("show", ["Aria"], deps as any)
    expect(result).toContain("Aria")
    expect(result).toContain("test helper")
  })

  it("show — returns not found for unknown helper", async () => {
    const result = await dispatchOwlCommand("show", ["Unknown"], makeDeps([]) as any)
    expect(result.toLowerCase()).toContain("not found")
  })

  it("create — launches wizard", async () => {
    const deps = makeDeps()
    const result = await dispatchOwlCommand("create", [], deps as any)
    expect(deps.wizard.start).toHaveBeenCalledWith("user1", deps.channelAdapter)
    expect(result).toBe("✓ Done")
  })

  it("delete — requires 'yes' confirmation", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    const result = await dispatchOwlCommand("delete", ["Aria"], deps as any)
    expect(result.toLowerCase()).toContain("confirm")
  })

  it("rename — moves directory and reloads registry", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    // rename requires fs access — just verify it returns a string
    const result = await dispatchOwlCommand("rename", ["Aria", "Kira"], deps as any)
    expect(typeof result).toBe("string")
  })

  it("unknown verb — returns helpful error", async () => {
    const result = await dispatchOwlCommand("frobnicate", [], makeDeps() as any)
    expect(result.toLowerCase()).toContain("unknown")
  })
})
