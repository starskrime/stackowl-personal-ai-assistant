import { describe, it, expect, vi } from "vitest";
import { CapabilityScanner } from "../src/heartbeat/capability-scanner.js";
import { ToolTracker } from "../src/tools/tracker.js";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));
vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockRejectedValue(new Error("no file")),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(false),
}));

describe("CapabilityScanner importantTools", () => {
  it("uses ToolTracker top tools instead of hardcoded list", () => {
    const mockTracker = {
      getTopBySelectionCount: vi.fn().mockReturnValue([
        { name: "my_custom_tool", stats: { selectionCount: 100 } },
      ]),
    } as unknown as ToolTracker;

    const mockRegistry = {
      getAllDefinitions: vi.fn().mockReturnValue([
        { name: "my_custom_tool" },
      ]),
    };
    const mockSkillsRegistry = {
      listEnabled: vi.fn().mockReturnValue([]),
    };

    const scanner = new CapabilityScanner(
      {} as any,
      mockRegistry as any,
      mockSkillsRegistry as any,
      undefined,
      mockTracker,
    );

    const result = scanner.scan();
    expect(result.gaps.some(g => g.name === "my_custom_tool")).toBe(true);
  });

  it("returns no tool_without_skill gaps when no toolTracker provided", () => {
    const scanner = new CapabilityScanner({} as any);
    const result = scanner.scan();
    const toolGaps = result.gaps.filter(g => g.type === "tool_without_skill");
    expect(toolGaps.length).toBe(0);
  });
});
