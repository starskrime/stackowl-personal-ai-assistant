import { describe, it, expect } from "vitest";
import {
  IntelligenceRouter,
  TASK_TYPE_DEFAULTS,
  type IntelligenceConfig,
} from "../src/intelligence/router.js";

function makeConfig(overrides?: Partial<IntelligenceConfig>): IntelligenceConfig {
  return {
    tiers: {
      high: { provider: "anthropic", model: "claude-opus-4-7" },
      mid:  { provider: "anthropic", model: "claude-sonnet-4-6" },
      low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
    },
    defaults: {
      parliament:   "high",
      extraction:   "low",
      conversation: "mid",
    },
    ...overrides,
  };
}

describe("IntelligenceRouter", () => {
  it("resolves parliament to high tier", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-opus-4-7");
    expect(result.tier).toBe("high");
  });

  it("resolves extraction to low tier", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("extraction");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-haiku-4-5-20251001");
    expect(result.tier).toBe("low");
  });

  it("falls back to mid tier when task type not in defaults", () => {
    const router = new IntelligenceRouter(
      makeConfig({ defaults: {} }),
      "anthropic",
      "claude-sonnet-4-6",
    );
    const result = router.resolve("evolution");
    expect(result.tier).toBe("mid");
    expect(result.model).toBe("claude-sonnet-4-6");
  });

  it("applies provider override", () => {
    const config = makeConfig({
      overrides: { parliament: { provider: "openai", model: "gpt-4o" } },
    });
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("openai");
    expect(result.model).toBe("gpt-4o");
    expect(result.tier).toBe("high");
  });

  it("applies partial model-only override", () => {
    const config = makeConfig({
      overrides: { parliament: { model: "claude-opus-4-7-custom" } },
    });
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-opus-4-7-custom");
  });

  it("falls back to fallback provider/model when mid tier not configured", () => {
    const config: IntelligenceConfig = {
      tiers: {
        high: { provider: "anthropic", model: "claude-opus-4-7" },
        mid:  { provider: "", model: "" },
        low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
      },
      defaults: {},
    };
    const router = new IntelligenceRouter(config, "ollama", "llama3.2");
    const result = router.resolve("evolution");
    expect(result.provider).toBe("ollama");
    expect(result.model).toBe("llama3.2");
  });

  it("TASK_TYPE_DEFAULTS covers all 9 task types", () => {
    const types = [
      "conversation", "parliament", "evolution", "extraction",
      "episodic", "classification", "synthesis", "summarization", "clarification",
    ];
    for (const t of types) {
      expect(TASK_TYPE_DEFAULTS).toHaveProperty(t);
    }
  });
});
