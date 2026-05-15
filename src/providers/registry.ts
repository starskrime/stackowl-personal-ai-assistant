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
import { RateLimitedProvider, concurrencyGate, providerRateLimiter } from "../ratelimit/index.js";

export type ProviderRole =
  | "chat-default"
  | "skill-router"
  | "semantic-disambiguator"
  | "vision"
  | "embedder"
  | "synthesizer"
  | "tool-judge";

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
  private roles: Map<ProviderRole, string> = new Map(); // role → provider name

  /**
   * Assign a role to a named provider.
   * Called during boot wiring. Later calls overwrite earlier ones (explicit config wins).
   */
  assignRole(role: ProviderRole, providerName: string): void {
    if (!this.providers.has(providerName)) {
      log.engine.warn(`[ProviderRegistry] Cannot assign role "${role}" to unregistered provider "${providerName}"`);
      return;
    }
    this.roles.set(role, providerName);
    log.engine.debug("provider.role.assigned", { role, providerName });
  }

  /**
   * Get a provider by role.
   * Falls back to the default provider with a warn if no role mapping exists.
   */
  byRole(role: ProviderRole): ModelProvider {
    const name = this.roles.get(role);
    if (!name) {
      log.engine.warn(`[ProviderRegistry] No provider assigned for role "${role}" — using default`, undefined, { role });
      return this.getDefault();
    }
    return this.get(name);
  }

  /**
   * Auto-assign roles based on provider type (called after all providers are registered).
   * Does NOT overwrite roles already explicitly assigned.
   */
  autoAssignRoles(providers: Array<{ name: string; type?: string }>): void {
    for (const { name, type } of providers) {
      if (!this.providers.has(name)) continue;
      if (type === "anthropic") {
        if (!this.roles.has("semantic-disambiguator")) this.assignRole("semantic-disambiguator", name);
        if (!this.roles.has("synthesizer")) this.assignRole("synthesizer", name);
        if (!this.roles.has("tool-judge")) this.assignRole("tool-judge", name);
      }
      if (type === "openai") {
        if (!this.roles.has("vision")) this.assignRole("vision", name);
      }
    }
  }

  /**
   * Register a provider instance directly (for testing only).
   * @internal
   */
  _registerForTest(name: string, provider: ModelProvider): void {
    this.providers.set(name, provider);
  }

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
        const rawProvider = factory(config, syntheticDef);
        const provider = new RateLimitedProvider(
          rawProvider,
          providerRateLimiter,
          config.name,
          concurrencyGate,
        );
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
          `[ProviderRegistry] Failed to initialize "${config.name}"`,
          error as Error,
          { provider: config.name },
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
      const rawProvider = factory(config, modelDef!);
      const provider = new RateLimitedProvider(
        rawProvider,
        providerRateLimiter,
        config.name,
        concurrencyGate,
      );
      this.providers.set(config.name, provider);
      // Anthropic providers open the circuit after a single 429 — error 2062
      // is a concurrent-request limit that retrying immediately makes worse.
      const failureThreshold =
        modelDef!.compatible === "anthropic" ? 1 : this.healthPolicy.failureThreshold;
      this.breakers.set(
        config.name,
        new ProviderCircuitBreaker(failureThreshold, this.healthPolicy.recoveryTimeoutMs),
      );
    } catch (error) {
      log.engine.warn(
        `[ProviderRegistry] Failed to initialize "${config.name}"`,
        error as Error,
        { provider: config.name },
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
   * Uses getState() (pure read) rather than isOpen() to avoid the OPEN→HALF_OPEN
   * side effect that would cause isOpen() to return false before recordResult() runs,
   * masking the transition and preventing notifyCircuitClosed() from ever firing.
   */
  recordProviderResult(name: string, success: boolean): void {
    log.engine.debug("provider-registry.recordProviderResult: entry", { name, success });
    const breaker = this.breakers.get(name);
    if (!breaker) {
      log.engine.debug("provider-registry.recordProviderResult: no breaker", { name });
      return;
    }
    const prevState = breaker.getState();
    breaker.recordResult(success);
    const newState = breaker.getState();
    if (newState === "OPEN" && prevState !== "OPEN") {
      log.engine.warn(`[ProviderRegistry] Circuit opened for "${name}" — notifying concurrency gate`);
      concurrencyGate.notifyCircuitOpen();
    } else if (newState === "CLOSED" && prevState !== "CLOSED") {
      log.engine.debug(`[ProviderRegistry] Circuit closed for "${name}" — notifying concurrency gate`);
      concurrencyGate.notifyCircuitClosed();
    }
    log.engine.debug("provider-registry.recordProviderResult: exit", { name, prevState, newState });
  }

  /**
   * Check whether a provider's circuit is currently open (failing).
   * Detects the OPEN→HALF_OPEN transition (triggered by isOpen()'s side effect)
   * and notifies the gate so the recovery probe can get through.
   */
  isProviderOpen(name: string): boolean {
    const breaker = this.breakers.get(name);
    if (!breaker) return false;
    const prevState = breaker.getState();
    const open = breaker.isOpen(); // may transition OPEN→HALF_OPEN as a side effect
    const newState = breaker.getState();
    if (prevState === "OPEN" && newState === "HALF_OPEN") {
      log.engine.debug(`[ProviderRegistry] "${name}" circuit → HALF_OPEN — unblocking gate for recovery probe`);
      concurrencyGate.notifyCircuitClosed();
    }
    return open;
  }

  /**
   * Return the configured default provider name, or null when no default has
   * been set yet (e.g. during startup or in tests with a partially-wired
   * registry). Never throws.
   */
  getDefaultName(): string | null {
    return this.defaultProviderName;
  }

  listProviders(): string[] {
    return Array.from(this.providers.keys());
  }

  /**
   * Remove a provider from the registry, clearing its circuit breaker, any role
   * assignments, and the default pointer if it was the current default.
   * Safe to call with an unknown name — silently does nothing.
   */
  deregister(name: string): void {
    log.engine.debug("provider-registry.deregister: entry", { name });
    this.providers.delete(name);
    this.breakers.delete(name);
    for (const [role, assigned] of this.roles) {
      if (assigned === name) this.roles.delete(role);
    }
    if (this.defaultProviderName === name) {
      this.defaultProviderName = null;
    }
    log.engine.debug("provider-registry.deregister: exit", { name });
  }

  async healthCheckAll(): Promise<Record<string, boolean>> {
    const results: Record<string, boolean> = {};
    for (const [name, provider] of this.providers) {
      results[name] = await provider.healthCheck();
    }
    return results;
  }
}
