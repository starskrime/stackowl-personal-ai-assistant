import { describe, it, expect } from "vitest";

describe("AnthropicNativeProvider — missing model config", () => {
  it("throws when defaultModel is absent", async () => {
    const { AnthropicNativeProvider } = await import("../../src/providers/anthropic-native.js");
    expect(
      () => new AnthropicNativeProvider({ name: "anthropic", apiKey: "test-key" } as any),
    ).toThrow("[Anthropic] No model configured");
  });

  it("constructs successfully when defaultModel is set", async () => {
    const { AnthropicNativeProvider } = await import("../../src/providers/anthropic-native.js");
    expect(
      () => new AnthropicNativeProvider({ name: "anthropic", defaultModel: "claude-sonnet-4-6", apiKey: "test-key" } as any),
    ).not.toThrow();
  });
});

describe("OpenAIProtocolProvider — missing model config", () => {
  it("throws when both activeModel and defaultModel are absent", async () => {
    const { OpenAIProtocolProvider } = await import("../../src/providers/protocols/openai.js");
    expect(
      () => new OpenAIProtocolProvider({ name: "openai", apiKey: "test-key" } as any, "https://api.openai.com/v1"),
    ).toThrow("[OpenAI] No model configured");
  });

  it("constructs successfully when defaultModel is set", async () => {
    const { OpenAIProtocolProvider } = await import("../../src/providers/protocols/openai.js");
    expect(
      () => new OpenAIProtocolProvider(
        { name: "openai", defaultModel: "gpt-4o", apiKey: "test-key" } as any,
        "https://api.openai.com/v1",
      ),
    ).not.toThrow();
  });
});
