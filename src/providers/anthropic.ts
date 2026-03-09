/**
 * StackOwl — Anthropic Provider (LangChain Backed)
 *
 * Connects to Anthropic Claude APIs via `@langchain/anthropic`.
 */

import { ChatAnthropic } from "@langchain/anthropic";
import { LangChainProvider } from "./langchain.js";
import type { ProviderConfig } from "./base.js";

export function createAnthropicProvider(config: ProviderConfig): LangChainProvider {
    const defaultModel = config.defaultModel ?? 'claude-3-5-sonnet-latest';

    // Requires ANTHROPIC_API_KEY env var, or passed via config
    const chatModel = new ChatAnthropic({
        model: defaultModel,
        anthropicApiKey: config.apiKey ?? process.env.ANTHROPIC_API_KEY,
    });

    // Anthropic doesn't have a public embedding API directly in this namespace yet,
    // so we skip embeddings for the fallback provider. If it's used for evolution,
    // evolution only requires ChatResponse anyway.
    return new LangChainProvider('anthropic', chatModel, defaultModel, undefined);
}
