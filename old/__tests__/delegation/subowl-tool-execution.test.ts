// __tests__/delegation/subowl-tool-execution.test.ts
import { describe, it, expect, vi } from "vitest"
import { SubOwlRunner } from "../../src/delegation/sub-owl-runner.js"

function makeMockProvider(responses: string[]) {
  let i = 0
  return {
    name: "mock",
    chat: vi.fn().mockImplementation(async () => ({
      content: responses[i++] ?? "final answer",
      model: "m",
      finishReason: "stop",
    })),
    listModels: vi.fn().mockResolvedValue([]),
  }
}

describe("SubOwlRunner tool execution", () => {
  it("invokes tool from registry when LLM response contains tool call", async () => {
    const toolCalled = vi.fn().mockResolvedValue("search result: cat facts")
    const registry = new Map([
      ["web_search", { execute: toolCalled, name: "web_search" }],
    ])

    // Provider first says "call tool: web_search {query:'cats'}", then says final answer
    const provider = makeMockProvider([
      JSON.stringify({ tool: "web_search", args: { query: "cats" } }),
      "Here are some cat facts based on the search results.",
    ])

    const runner = new SubOwlRunner(
      provider as any,
      registry as any,
      "You are a helpful assistant.",
      "/workspace",
      2,
    )

    const result = await runner.run([{
      id: "t1",
      description: "Find cat facts",
      tools: ["web_search"],
      dependsOn: [],
      expectedOutput: "cat facts",
      args: { query: "cats" },
    }])

    expect(toolCalled).toHaveBeenCalled()
    expect(result.length).toBeGreaterThan(0)
  })

  it("handles tool not in registry gracefully", async () => {
    const provider = makeMockProvider([
      JSON.stringify({ tool: "unknown_tool", args: {} }),
      "I cannot use that tool.",
    ])
    const registry = new Map() // empty

    const runner = new SubOwlRunner(provider as any, registry as any, "You help.", "/workspace", 2)
    const result = await runner.run([{
      id: "t1", description: "test", tools: [], dependsOn: [], expectedOutput: "anything",
    }])

    expect(result.length).toBeGreaterThan(0)
    // Should not throw
  })
})
