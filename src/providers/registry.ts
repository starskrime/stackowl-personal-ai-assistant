/**
 * StackOwl — Provider Registry
 *
 * Model-file-driven provider factory.
 *
 * Resolution order for a config entry named "X":
 *   1. Look up model file at src/models/X  (or src/models/<profile> if profile is set)
 *   2. Read `compatible` field → pick one of 4 protocol implementations
 *   3. Create provider instance with merged config (model file defaults + user overrides)
 *
 * Adding a new provider = add a file to src/models/.
 * No registry edits required.
 *
 * Protocols:
 *   openai    → OpenAI SDK (openai, ollama, minimax, lmstudio, openrouter, etc.)
 *   anthropic → @anthropic-ai/sdk
 *   gemini    → @google/genai
 *   grok      → OpenAI SDK (groq.com, xAI, etc.)
 */

import { log } from "../logger.js";
import { getModelLoader } from "../models/loader.js";
import type { ModelProvider, ProviderConfig } from "./base.js";
import { OpenAIProtocolProvider } from "./protocols/openai.js";
import { createAnthropicProvider } from "./protocols/anthropic.js";
import { GeminiProtocolProvider } from "./protocols/gemini.js";
import { GrokProtocolProvider } from "./protocols/grok.js";
import type { ModelDefinition } from "../models/loader.js";
import { ProviderCircuitBreaker } from "./circuit-breaker.js";
import type { HealthPolicy } from "../intelligence/router.js";

// ─── Protocol Factories ───────────────────────────────────────────

type ProtocolFactory = (
  config: ProviderConfig,
  modelDef: ModelDefinition,
) => ModelProvider;

const PROTOCOL_FACTORIES: Record<string, ProtocolFactory> = {
  openai: (config, def) =>
    new OpenAIProtocolProvider(
      {
        ...config,
        baseUrl: config.baseUrl ?? def.url,
        defaultModel:
          (config as any).activeModel ?? config.defaultModel ?? def.defaultModel,
      },
      config.baseUrl ?? def.url,
    ),

  anthropic: (config, def) => createAnthropicProvider(config, def),

  gemini: (config, def) => new GeminiProtocolProvider(config, def),

  grok: (config, def) =>
    new GrokProtocolProvider(
      {
        ...config,
        baseUrl: config.baseUrl ?? def.url,
        defaultModel:
          (config as any).activeModel ?? config.defaultModel ?? def.defaultModel,
      },
      def,
    ),
};

// ─── Registry ────────────────────────────────────────────────────

export class ProviderRegistry {
  private providers: Map<string, ModelProvider> = new Map();
  private defaultProviderName: string | null = null;
  private breakers: Map<string, ProviderCircuitBreaker> = new Map();
  private healthPolicy: HealthPolicy = { failureThreshold: 5, recoveryTimeoutMs: 30_000 };

  /**
   * Register a provider from config.
   *
   * Resolves protocol via model file:
   *   - Uses config.profile (if set) or config.name as the model file key
   *   - Falls back to openai protocol when a baseUrl is configured but no model file exists
   */
  register(config: ProviderConfig): void {
    const modelKey = config.profile ?? config.name;
    const loader = getModelLoader();
    const modelDef = loader.get(modelKey);

    let factory: ProtocolFactory | undefined;

    if (modelDef) {
      factory = PROTOCOL_FACTORIES[modelDef.compatible];
      if (!factory) {
        log.engine.warn(
          `[ProviderRegistry] Unknown protocol "${modelDef.compatible}" in model file "${modelKey}". ` +
            `Available: ${Object.keys(PROTOCOL_FACTORIES).join(", ")}`,
        );
        return;
      }
    } else if (config.baseUrl) {
      // No model file — fall back to OpenAI protocol if baseUrl is configured
      log.engine.debug(
        `[ProviderRegistry] No model file for "${modelKey}". ` +
          `Falling back to openai protocol (baseUrl: ${config.baseUrl})`,
      );
      const syntheticDef: ModelDefinition = {
        name: modelKey,
        compatible: "openai",
        availableModels: [config.defaultModel ?? (config as any).activeModel ?? "default"],
        defaultModel: config.defaultModel ?? (config as any).activeModel ?? "default",
        url: config.baseUrl,
      };
      factory = PROTOCOL_FACTORIES.openai;
      try {
        const provider = factory(config, syntheticDef);
        this.providers.set(config.name, provider);
        this.breakers.set(
          config.name,
          new ProviderCircuitBreaker(
            this.healthPolicy.failureThreshold,
            this.healthPolicy.recoveryTimeoutMs,
          ),
        );
      } catch (error) {
        log.engine.warn(
          `[ProviderRegistry] Failed to initialize "${config.name}": ${(error as Error).message}`,
        );
      }
      return;
    } else {
      log.engine.warn(
        `[ProviderRegistry] No model file for "${modelKey}" and no baseUrl configured. ` +
          `Create src/models/${modelKey} or set a baseUrl.`,
      );
      return;
    }

    try {
      const provider = factory(config, modelDef!);
      this.providers.set(config.name, provider);
      this.breakers.set(
        config.name,
        new ProviderCircuitBreaker(
          this.healthPolicy.failureThreshold,
          this.healthPolicy.recoveryTimeoutMs,
        ),
      );
    } catch (error) {
      log.engine.warn(
        `[ProviderRegistry] Failed to initialize "${config.name}": ${(error as Error).message}`,
      );
    }
  }

  /**
   * Set the default provider by name.
   */
  setDefault(name: string): void {
    if (!this.providers.has(name)) {
      // If the requested default was not registered (e.g. missing model file),
      // fall back to the first registered provider rather than crashing.
      const first = this.providers.keys().next().value;
      if (first) {
        log.engine.warn(
          `[ProviderRegistry] Default provider "${name}" not registered. ` +
            `Using "${first}" instead.`,
        );
        this.defaultProviderName = first;
        return;
      }
      throw new Error(
        `[ProviderRegistry] Cannot set default: provider "${name}" not registered and no fallback available.`,
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

  getDefault(): ModelProvider {
    return this.get();
  }

  /** Configure circuit breaker parameters from IntelligenceConfig.healthPolicy. */
  setHealthPolicy(policy: HealthPolicy): void {
    this.healthPolicy = policy;
  }

  /**
   * Get a provider if its circuit breaker is not OPEN.
   * Returns null if the provider is OPEN (caller should try a fallback).
   * Returns the provider instance if CLOSED or HALF_OPEN.
   */
  getAvailable(name?: string): ModelProvider | null {
    const targetName = name ?? this.defaultProviderName;
    if (!targetName) return null;

    const breaker = this.breakers.get(targetName);
    if (breaker?.isOpen()) {
      log.engine.warn(
        `[ProviderRegistry] Provider "${targetName}" circuit is OPEN — skipping`,
      );
      return null;
    }

    const provider = this.providers.get(targetName);
    return provider ?? null;
  }

  /**
   * Record the outcome of a provider API call.
   * Updates the circuit breaker state for the named provider.
   */
  recordProviderResult(name: string, success: boolean): void {
    this.breakers.get(name)?.recordResult(success);
  }

  /**
   * Check whether a provider's circuit is currently open (failing).
   */
  isProviderOpen(name: string): boolean {
    return this.breakers.get(name)?.isOpen() ?? false;
  }

  listProviders(): string[] {
    return Array.from(this.providers.keys());
  }

  async healthCheckAll(): Promise<Record<string, boolean>> {
    const results: Record<string, boolean> = {};
    for (const [name, provider] of this.providers) {
      results[name] = await provider.healthCheck();
    }
    return results;
  }
}
