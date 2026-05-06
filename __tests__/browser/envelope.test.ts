import { describe, it, expect } from "vitest";
import { isWebToolResult, parseWebToolResult, serializeWebToolResult, type WebToolResult } from "../../src/browser/envelope.js";

describe("envelope TierName + TierOutcome (Element 16c)", () => {
  it("rejects 'http' tier name", () => {
    const r: unknown = {
      success: false,
      error: {
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [{ tier: 1, name: "http", outcome: "blocked", durationMs: 100 }],
      },
    };
    expect(isWebToolResult(r)).toBe(false);
  });

  it("accepts 'scrapling', 'camofox', 'obscura' tier names", () => {
    for (const name of ["scrapling", "camofox", "obscura"] as const) {
      const r: WebToolResult = {
        success: false,
        error: {
          code: "BLOCKED_BY_ANTI_BOT",
          message: "blocked",
          attemptedTiers: [{ tier: 1, name, outcome: "blocked", durationMs: 100 }],
        },
      };
      expect(isWebToolResult(r)).toBe(true);
    }
  });

  it("accepts new TierOutcome values", () => {
    for (const outcome of ["skipped-by-learned-routing", "skipped-disabled"] as const) {
      const r: WebToolResult = {
        success: false,
        error: {
          code: "ALL_TIERS_UNAVAILABLE",
          message: "all unavailable",
          attemptedTiers: [{ tier: 3, name: "obscura", outcome, durationMs: 0 }],
        },
      };
      expect(isWebToolResult(r)).toBe(true);
    }
  });

  it("round-trips serialize/parse for the new shape", () => {
    const r: WebToolResult = {
      success: false,
      error: {
        code: "ALL_TIERS_UNAVAILABLE",
        message: "all tiers exhausted",
        attemptedTiers: [
          { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 200, blockedReason: "cloudflare" },
          { tier: 2, name: "camofox", outcome: "unavailable", durationMs: 0 },
          { tier: 3, name: "obscura", outcome: "skipped-disabled", durationMs: 0 },
        ],
        suggestedEscalation: "live_browser",
      },
    };
    const round = parseWebToolResult(serializeWebToolResult(r));
    expect(round).toEqual(expect.objectContaining({ success: false }));
  });
});
