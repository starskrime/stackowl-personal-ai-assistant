import { describe, it, expect } from "vitest";
import { formatSignalPromoted } from "../../src/gateway/narration-formatter.js";

describe("formatSignalPromoted", () => {
  it("renders the canonical template", () => {
    const out = formatSignalPromoted({
      type: "signal:promoted",
      signal: {
        id: "s",
        source: "git",
        priority: "high",
        title: "12 uncommitted files in src/signals/",
        content: "...",
        timestamp: 0,
        ttlMs: 60_000,
      },
      goal: { id: "g", title: "ship Element 16b" },
      rationale: "advances goal scope",
      verdict: "ADVANCES",
    });
    expect(out).toBe(
      `🔭 [git] 12 uncommitted files in src/signals/ — advances "ship Element 16b" (verdict: ADVANCES)`,
    );
  });
});
