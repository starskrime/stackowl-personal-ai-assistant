import { describe, it, expect } from "vitest";
import { buildChallengeDirective, CHALLENGE_DIRECTIVES } from "../../src/intelligence/challenge-directive.js";

describe("buildChallengeDirective", () => {
  it("returns supportive for low challengeLevel", () => {
    expect(buildChallengeDirective(1)).toBe(CHALLENGE_DIRECTIVES.low);
    expect(buildChallengeDirective(3)).toBe(CHALLENGE_DIRECTIVES.low);
  });

  it("returns honest for mid challengeLevel", () => {
    expect(buildChallengeDirective(4)).toBe(CHALLENGE_DIRECTIVES.medium);
    expect(buildChallengeDirective(6)).toBe(CHALLENGE_DIRECTIVES.medium);
  });

  it("returns assertive for high challengeLevel", () => {
    expect(buildChallengeDirective(7)).toBe(CHALLENGE_DIRECTIVES.high);
    expect(buildChallengeDirective(10)).toBe(CHALLENGE_DIRECTIVES.high);
  });
});
