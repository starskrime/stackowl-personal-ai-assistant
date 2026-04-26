import { describe, it, expect } from "vitest";
import { ModelRouter } from "../src/engine/router.js";
import type { StackOwlConfig } from "../src/config/loader.js";

function makeConfig(models: Array<{ modelName: string; providerName: string }>): StackOwlConfig {
  return {
    defaultProvider: "ollama",
    defaultModel: "llama3.2",
    workspace: "./workspace",
    providers: { ollama: { baseUrl: "http://localhost:11434", apiKey: "", defaultModel: "llama3.2", type: "ollama" } },
    smartRouting: {
      enabled: true,
      availableModels: models,
    },
  } as unknown as StackOwlConfig;
}

describe("ModelRouter", () => {
  it("returns providerName on simple tier", () => {
    const config = makeConfig([
      { modelName: "llama3.2", providerName: "ollama" },
      { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
    ]);
    const result = ModelRouter.route("hi", config, 0);
    expect(result.providerName).toBe("ollama");
    expect(result.modelName).toBe("llama3.2");
  });

  it("returns providerName on heavy tier", () => {
    const config = makeConfig([
      { modelName: "llama3.2", providerName: "ollama" },
      { modelName: "claude-sonnet-4-6", providerName: "anthropic" },
    ]);
    const result = ModelRouter.route("implement a full TypeScript compiler with AST", config, 0);
    expect(result.providerName).toBe("anthropic");
    expect(result.modelName).toBe("claude-sonnet-4-6");
  });

  it("returns providerName when roster has exactly 1 entry", () => {
    const config = makeConfig([{ modelName: "gpt-4o", providerName: "openai" }]);
    const result = ModelRouter.route("hello", config, 0);
    expect(result.modelName).toBe("gpt-4o");
    expect(result.providerName).toBe("openai");
  });

  it("failure fallback returns both fields", () => {
    const config = {
      ...makeConfig([]),
      smartRouting: {
        enabled: true,
        availableModels: [],
        fallbackProvider: "anthropic",
        fallbackModel: "claude-sonnet-4-6",
      },
    } as unknown as StackOwlConfig;
    const result = ModelRouter.route("hi", config, 2);
    expect(result.providerName).toBe("anthropic");
    expect(result.modelName).toBe("claude-sonnet-4-6");
  });
});
