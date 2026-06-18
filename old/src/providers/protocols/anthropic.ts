/**
 * StackOwl — Anthropic Protocol Implementation
 *
 * Handles providers with compatible: anthropic
 * Uses the official @anthropic-ai/sdk.
 *
 * Re-exports AnthropicNativeProvider with a factory wrapper.
 */

import type { ModelProvider, ProviderConfig } from "../base.js";
import { AnthropicNativeProvider } from "../anthropic-native.js";
import type { ModelDefinition } from "../../models/loader.js";

export function createAnthropicProvider(
  config: ProviderConfig,
  modelDef: ModelDefinition,
): ModelProvider {
  return new AnthropicNativeProvider({
    ...config,
    baseUrl: config.baseUrl ?? modelDef.url,
    defaultModel:
      (config as any).activeModel ?? config.defaultModel ?? modelDef.defaultModel,
  });
}
