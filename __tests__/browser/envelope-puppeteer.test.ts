import { describe, it, expect } from "vitest";
import { isWebToolError } from "../../src/browser/envelope.js";

describe("envelope TierName puppeteer", () => {
  it("isWebToolError accepts tier attempt with name 'puppeteer'", () => {
    expect(
      isWebToolError({
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [
          { tier: 3, name: "puppeteer", durationMs: 100, outcome: "blocked" },
        ],
      }),
    ).toBe(true);
  });

  it("isWebToolError rejects unknown tier name", () => {
    expect(
      isWebToolError({
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [
          { tier: 3, name: "unknown_tier", durationMs: 100, outcome: "blocked" },
        ],
      }),
    ).toBe(false);
  });
});
