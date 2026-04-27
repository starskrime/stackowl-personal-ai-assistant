import { describe, it, expect } from "vitest";
import type {
  SpecializedOwlSpec,
  SpecializedPersonality,
  SpecializedModel,
  SpecializedPermissions,
  SpecializedRoutingRules,
  SpecializedSkills,
} from "../../src/owls/specialized-types.js";

describe("SpecializedOwlSpec", () => {
  it("should have correct interface shape", () => {
    const spec: SpecializedOwlSpec = {
      name: "TradingBot",
      role: "Stock trading assistant",
      emoji: "📈",
      personality: {
        challengeLevel: "high",
        verbosity: "balanced",
        tone: "casual but precise",
      },
      expertise: ["stock market", "portfolio"],
      model: {
        provider: "anthropic",
        model: "claude-sonnet-4-20250514",
        maxTokens: 4096,
      },
      permissions: {
        allowedTools: ["shell", "calculator"],
        deniedTools: ["write", "edit"],
        capabilityConstraints: ["Cannot execute trades directly"],
      },
      routingRules: {
        keywords: ["stock", "trading", "portfolio"],
      },
      skills: {
        allowed: ["trading-strategies"],
      },
      credentialsPath: "/path/to/credentials",
    };
    expect(spec.name).toBe("TradingBot");
    expect(spec.permissions.deniedTools).toContain("write");
  });

  it("should allow optional credentialsPath", () => {
    const spec: SpecializedOwlSpec = {
      name: "Researcher",
      role: "Research assistant",
      emoji: "🔬",
      personality: {
        challengeLevel: "medium",
        verbosity: "verbose",
        tone: "academic",
      },
      expertise: ["research", "analysis"],
      model: {
        provider: "openai",
        model: "gpt-4",
      },
      permissions: {
        allowedTools: [],
        deniedTools: [],
        capabilityConstraints: [],
      },
      routingRules: {
        keywords: ["research", "analyze"],
      },
      skills: {
        allowed: [],
      },
    };
    expect(spec.credentialsPath).toBeUndefined();
  });
});
