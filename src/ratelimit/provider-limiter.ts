/**
 * StackOwl — Rate-Limited Provider Wrapper
 *
 * Wraps a ModelProvider with sliding-window rate limiting and a concurrency
 * gate. Both checks fire on every non-embedding call.
 *
 *   1. checkLimit()  — sliding-window count (rejects if > N calls/minute)
 *   2. gate.acquire() — semaphore (blocks if maxConcurrent in-flight)
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
import type { ConcurrencyGate } from "./concurrency-gate.js";
import { log } from "../logger.js";

export class RateLimitedProvider implements ModelProvider {
  readonly name: string;

  constructor(
    private inner: ModelProvider,
    private limiter: RateLimiter,
    private providerKey: string,
    private gate: ConcurrencyGate,
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
      throw new Error(`Rate limited (${result.rule}): retry after ${waitSec}s`);
    }
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    log.engine.debug("rate-limited-provider.chat: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      const result = await this.inner.chat(messages, model, options);
      log.engine.debug("rate-limited-provider.chat: exit", { provider: this.providerKey });
      return result;
    } finally {
      release();
    }
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    log.engine.debug("rate-limited-provider.chatWithTools: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      const result = await this.inner.chatWithTools(messages, tools, model, options);
      log.engine.debug("rate-limited-provider.chatWithTools: exit", { provider: this.providerKey });
      return result;
    } finally {
      release();
    }
  }

  // Stream methods must be async generators so `await` is legal inside them.
  // The return type AsyncGenerator<T> is compatible with the ModelProvider interface.

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    log.engine.debug("rate-limited-provider.chatStream: entry", { provider: this.providerKey });
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      yield* this.inner.chatStream(messages, model, options);
    } finally {
      release();
      log.engine.debug("rate-limited-provider.chatStream: exit", { provider: this.providerKey });
    }
  }

  async *chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    log.engine.debug("rate-limited-provider.chatWithToolsStream: entry", { provider: this.providerKey });
    if (!this.inner.chatWithToolsStream) {
      throw new Error(`Provider ${this.name} does not support chatWithToolsStream`);
    }
    this.checkLimit();
    const release = await this.gate.acquire();
    try {
      yield* this.inner.chatWithToolsStream(messages, tools, model, options);
    } finally {
      release();
      log.engine.debug("rate-limited-provider.chatWithToolsStream: exit", { provider: this.providerKey });
    }
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    // Embeddings are lightweight — skip rate limit and gate
    return this.inner.embed(text, model);
  }

  async listModels(): Promise<string[]> {
    return this.inner.listModels();
  }

  async healthCheck(): Promise<boolean> {
    return this.inner.healthCheck();
  }
}
