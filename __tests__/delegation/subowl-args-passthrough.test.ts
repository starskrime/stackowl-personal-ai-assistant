// __tests__/delegation/subowl-args-passthrough.test.ts
import { describe, it, expect, vi } from "vitest"
import { SubOwlExecutor } from "../../src/delegation/subowl-executor.js"
import type { ToolImplementation } from "../../src/tools/registry.js"

describe("SubOwlExecutor args passthrough", () => {
  it("passes task.args to tool.execute — not empty {}", async () => {
    const capturedArgs: unknown[] = []
    const mockTool = {
      definition: { name: "web_search", description: "Search the web" },
      execute: vi.fn().mockImplementation(async (args: unknown) => {
        capturedArgs.push(args)
        return "result"
      }),
    } as unknown as ToolImplementation

    const mockRegistry = new Map<string, ToolImplementation>([
      ["web_search", mockTool],
    ])

    const executor = new SubOwlExecutor(mockRegistry)
    await executor.executeSubtask(
      {
        id: "t1",
        description: "Search for cats",
        tools: ["web_search"],
        dependsOn: [],
        expectedOutput: "list of cats",
        args: { query: "cats", maxResults: 5 },
      },
      { cwd: "/tmp" },
    )

    expect(mockTool.execute).toHaveBeenCalledWith(
      { query: "cats", maxResults: 5 },
      expect.anything(),
    )
  })

  it("passes empty {} when task.args is undefined", async () => {
    const capturedArgs: unknown[] = []
    const mockTool = {
      definition: { name: "list_files", description: "List files" },
      execute: vi.fn().mockImplementation(async (args: unknown) => {
        capturedArgs.push(args)
        return "ok"
      }),
    } as unknown as ToolImplementation

    const mockRegistry = new Map<string, ToolImplementation>([
      ["list_files", mockTool],
    ])

    const executor = new SubOwlExecutor(mockRegistry)
    await executor.executeSubtask(
      {
        id: "t1",
        description: "List files",
        tools: ["list_files"],
        dependsOn: [],
        expectedOutput: "file list",
        // args intentionally absent
      },
      { cwd: "/tmp" },
    )

    expect(mockTool.execute).toHaveBeenCalledWith({}, expect.anything())
  })
})
