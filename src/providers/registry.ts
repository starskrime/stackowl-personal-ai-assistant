/**
 * StackOwl — Provider Registry
 *
 * Factory for creating and managing model providers.
 * Ensures single instance per provider type.
 */

import type { ModelProvider, ProviderConfig } from './base.js';
import { createOllamaProvider } from './ollama.js';
import { createAnthropicProvider } from './anthropic.js';

type ProviderFactory = (config: ProviderConfig) => ModelProvider;

const BUILT_IN_FACTORIES: Record<string, ProviderFactory> = {
    ollama: createOllamaProvider,
    anthropic: createAnthropicProvider,
};

export class ProviderRegistry {
    private providers: Map<string, ModelProvider> = new Map();
    private defaultProviderName: string | null = null;

    /**
     * Register a provider from config.
     * Creates the provider instance and stores it.
     */
    register(config: ProviderConfig): void {
        const factory = BUILT_IN_FACTORIES[config.name];
        if (!factory) {
            throw new Error(
                `[ProviderRegistry] Unknown provider: "${config.name}". ` +
                `Available: ${Object.keys(BUILT_IN_FACTORIES).join(', ')}`
            );
        }

        try {
            const provider = factory(config);
            this.providers.set(config.name, provider);
        } catch (error) {
            console.warn(`[ProviderRegistry] Warning: Failed to initialize provider "${config.name}". It will be disabled. Reason: ${(error as Error).message}`);
        }
    }

    /**
     * Set the default provider by name.
     */
    setDefault(name: string): void {
        if (!this.providers.has(name)) {
            throw new Error(
                `[ProviderRegistry] Cannot set default: provider "${name}" not registered.`
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
                '[ProviderRegistry] No provider specified and no default set.'
            );
        }

        const provider = this.providers.get(targetName);
        if (!provider) {
            throw new Error(
                `[ProviderRegistry] Provider "${targetName}" not found. ` +
                `Registered: ${Array.from(this.providers.keys()).join(', ')}`
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
