import { describe, it, expect, vi } from "vitest";
import { ToolTracker } from "../src/tools/tracker.js";

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockRejectedValue(new Error("no file")),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(false),
}));

describe("ToolTracker.getTopBySelectionCount", () => {
  it("returns empty array when no stats", () => {
    const tracker = new ToolTracker("/tmp/fake");
    expect(tracker.getTopBySelectionCount(5)).toEqual([]);
  });

  it("returns tools sorted by selectionCount desc", () => {
    const tracker = new ToolTracker("/tmp/fake");
    tracker.recordSelection("web_crawl");
    tracker.recordSelection("read_file");
    tracker.recordSelection("web_crawl");
    const top = tracker.getTopBySelectionCount(2);
    expect(top[0].name).toBe("web_crawl");
    expect(top[0].stats.selectionCount).toBe(2);
  });

  it("respects limit n", () => {
    const tracker = new ToolTracker("/tmp/fake");
    for (let i = 0; i < 20; i++) {
      tracker.recordSelection(`tool_${i}`);
    }
    expect(tracker.getTopBySelectionCount(10).length).toBe(10);
  });
});
