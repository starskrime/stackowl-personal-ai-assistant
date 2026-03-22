/**
 * StackOwl — Rate-Limited Provider Wrapper
 *
 * Wraps a ModelProvider with rate limiting. Checks the limiter before
 * each API call and rejects with a clear error when rate limited.
 */

import type {
  ModelProvider,
  ChatMessage,
  ChatResponse,
  ChatOptions,
  ToolDefinition,
  StreamChunk,
  StreamEvent,
  EmbeddingResponse,
} from "../providers/base.js";
import type { RateLimiter } from "./limiter.js";
import { log } from "../logger.js";

export class RateLimitedProvider implements ModelProvider {
  readonly name: string;

  constructor(
    private inner: ModelProvider,
    private limiter: RateLimiter,
    private providerKey: string,
  ) {
    this.name = inner.name;
  }

  private checkLimit(): void {
    const result = this.limiter.consume(this.providerKey);
    if (!result.allowed) {
      const waitSec = Math.ceil((result.retryAfterMs ?? 1000) / 1000);
      log.engine.warn(
        `[RateLimitedProvider] ${this.providerKey} rate limited by "${result.rule}" — retry in ${waitSec}s`,
      );
      throw new Error(
        `Rate limited (${result.rule}): retry after ${waitSec}s`,
      );
    }
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    this.checkLimit();
    return this.inner.chat(messages, model, options);
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    this.checkLimit();
    return this.inner.chatWithTools(messages, tools, model, options);
  }

  chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    this.checkLimit();
    return this.inner.chatStream(messages, model, options);
  }

  chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    this.checkLimit();
    if (!this.inner.chatWithToolsStream) {
      throw new Error(
        `Provider ${this.name} does not support chatWithToolsStream`,
      );
    }
    return this.inner.chatWithToolsStream(messages, tools, model, options);
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    // Embeddings are lightweight — don't rate limit them
    return this.inner.embed(text, model);
  }

  async listModels(): Promise<string[]> {
    return this.inner.listModels();
  }

  async healthCheck(): Promise<boolean> {
    return this.inner.healthCheck();
  }
}
