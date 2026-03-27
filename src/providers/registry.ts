/**
 * StackOwl — Provider Registry
 *
 * Factory for creating and managing model providers.
 * Native drivers per provider — no LangChain abstraction.
 *
 * Supports:
 *   - ollama         → OllamaNativeProvider (local models)
 *   - anthropic      → AnthropicNativeProvider (Claude API)
 *   - openai-compatible → OpenAICompatProvider (OpenRouter, Together, LMStudio, vLLM, Groq, DeepSeek, MiniMax, etc.)
 *
 * Auto-detection: if the provider name is unknown, tries heuristics
 * based on baseUrl and apiKey to pick the right driver.
 */

import type { ModelProvider, ProviderConfig } from "./base.js";
import { OllamaNativeProvider } from "./ollama-native.js";
import { AnthropicNativeProvider } from "./anthropic-native.js";
import { OpenAICompatProvider } from "./openai-compat.js";
import { MiniMaxProvider } from "./minimax.js";

type ProviderFactory = (config: ProviderConfig) => ModelProvider;

const BUILT_IN_FACTORIES: Record<string, ProviderFactory> = {
  ollama: (config) => new OllamaNativeProvider(config),
  anthropic: (config) => new AnthropicNativeProvider(config),
  "openai-compatible": (config) => new OpenAICompatProvider(config),
  openai: (config) => new OpenAICompatProvider({ ...config, name: "openai" }),
  openrouter: (config) =>
    new OpenAICompatProvider({
      ...config,
      name: "openrouter",
      baseUrl: config.baseUrl ?? "https://openrouter.ai/api/v1",
    }),
  together: (config) =>
    new OpenAICompatProvider({
      ...config,
      name: "together",
      baseUrl: config.baseUrl ?? "https://api.together.xyz/v1",
    }),
  groq: (config) =>
    new OpenAICompatProvider({
      ...config,
      name: "groq",
      baseUrl: config.baseUrl ?? "https://api.groq.com/openai/v1",
    }),
  deepseek: (config) =>
    new OpenAICompatProvider({
      ...config,
      name: "deepseek",
      baseUrl: config.baseUrl ?? "https://api.deepseek.com/v1",
    }),
  lmstudio: (config) =>
    new OpenAICompatProvider({
      ...config,
      name: "lmstudio",
      baseUrl: config.baseUrl ?? "http://127.0.0.1:1234/v1",
    }),
  minimax: (config) => new MiniMaxProvider(config),
};

/**
 * Auto-detect which factory to use based on config hints.
 */
function detectFactory(config: ProviderConfig): ProviderFactory | null {
  const url = config.baseUrl?.toLowerCase() ?? "";
  const key = config.apiKey ?? "";

  // Ollama detection: default port or "ollama" in URL
  if (url.includes(":11434") || url.includes("ollama")) {
    return BUILT_IN_FACTORIES.ollama;
  }

  // Anthropic detection: API key pattern
  if (key.startsWith("sk-ant-")) {
    return BUILT_IN_FACTORIES.anthropic;
  }

  // OpenRouter detection
  if (url.includes("openrouter.ai")) {
    return BUILT_IN_FACTORIES.openrouter;
  }

  // Together detection
  if (url.includes("together.xyz") || url.includes("together.ai")) {
    return BUILT_IN_FACTORIES.together;
  }

  // Groq detection
  if (url.includes("groq.com")) {
    return BUILT_IN_FACTORIES.groq;
  }

  // DeepSeek detection
  if (url.includes("deepseek.com")) {
    return BUILT_IN_FACTORIES.deepseek;
  }

  // LMStudio detection: common ports
  if (url.includes(":1234")) {
    return BUILT_IN_FACTORIES.lmstudio;
  }

  // MiniMax detection (both .com and .io domains)
  if (url.includes("minimax.io") || url.includes("minimaxi.com")) {
    return BUILT_IN_FACTORIES.minimax;
  }

  // If a baseUrl is set but not recognized, assume OpenAI-compatible
  if (url) {
    return BUILT_IN_FACTORIES["openai-compatible"];
  }

  return null;
}

export class ProviderRegistry {
  private providers: Map<string, ModelProvider> = new Map();
  private defaultProviderName: string | null = null;

  /**
   * Register a provider from config.
   * Tries built-in factories first, then auto-detection.
   */
  register(config: ProviderConfig): void {
    let factory = BUILT_IN_FACTORIES[config.name];

    // Auto-detect if name is not in the factory map
    if (!factory) {
      const detected = detectFactory(config);
      if (detected) {
        factory = detected;
        console.log(
          `[ProviderRegistry] Auto-detected driver for "${config.name}" based on config`,
        );
      }
    }

    if (!factory) {
      throw new Error(
        `[ProviderRegistry] Unknown provider: "${config.name}". ` +
          `Available: ${Object.keys(BUILT_IN_FACTORIES).join(", ")}. ` +
          `Set a baseUrl to auto-detect, or use type: "openai-compatible".`,
      );
    }

    try {
      const provider = factory(config);
      this.providers.set(config.name, provider);
    } catch (error) {
      console.warn(
        `[ProviderRegistry] Warning: Failed to initialize provider "${config.name}". ` +
          `It will be disabled. Reason: ${(error as Error).message}`,
      );
    }
  }

  /**
   * Set the default provider by name.
   */
  setDefault(name: string): void {
    if (!this.providers.has(name)) {
      throw new Error(
        `[ProviderRegistry] Cannot set default: provider "${name}" not registered.`,
      );
    }
    this.defaultProviderName = name;
  }

  /**
   * Get a provider by name, or the default provider.
   */
  get(name?: string): ModelProvider {
    const targetName = name ?? this.defaultProviderName;

    if (!targetName) {
      throw new Error(
        "[ProviderRegistry] No provider specified and no default set.",
      );
    }

    const provider = this.providers.get(targetName);
    if (!provider) {
      throw new Error(
        `[ProviderRegistry] Provider "${targetName}" not found. ` +
          `Registered: ${Array.from(this.providers.keys()).join(", ")}`,
      );
    }

    return provider;
  }

  /**
   * Get the default provider.
   */
  getDefault(): ModelProvider {
    return this.get();
  }

  /**
   * List all registered provider names.
   */
  listProviders(): string[] {
    return Array.from(this.providers.keys());
  }

  /**
   * Run health checks on all registered providers.
   */
  async healthCheckAll(): Promise<Record<string, boolean>> {
    const results: Record<string, boolean> = {};
    for (const [name, provider] of this.providers) {
      results[name] = await provider.healthCheck();
    }
    return results;
  }
}
