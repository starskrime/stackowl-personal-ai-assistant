/**
 * StackOwl — Grok Protocol Implementation
 *
 * Handles providers with compatible: grok
 * (Groq inference platform, xAI Grok, and similar OpenAI-compatible APIs
 * with specific authentication or behavioral differences)
 *
 * Uses the official `openai` npm SDK pointed at the provider's base URL.
 */

import type { ModelProvider, ProviderConfig } from "../base.js";
import type { ModelDefinition } from "../../models/loader.js";
import { OpenAIProtocolProvider } from "./openai.js";

/**
 * GrokProtocolProvider is identical to OpenAIProtocolProvider in implementation —
 * both Groq (groq.com) and xAI Grok use OpenAI-compatible REST APIs.
 * The separate class exists for future protocol-level differentiation
 * (custom headers, rate-limit handling, model-specific quirks).
 */
export class GrokProtocolProvider extends OpenAIProtocolProvider {
  constructor(config: ProviderConfig, modelDef: ModelDefinition) {
    super(config, modelDef.url);
    // Ensure the model def's URL is used if not overridden in config
  }
}

export function createGrokProvider(
  config: ProviderConfig,
  modelDef: ModelDefinition,
): ModelProvider {
  return new GrokProtocolProvider(
    {
      ...config,
      baseUrl: config.baseUrl ?? modelDef.url,
      defaultModel:
        (config as any).activeModel ?? config.defaultModel ?? modelDef.defaultModel,
    },
    modelDef,
  );
}
