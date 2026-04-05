/**
 * Memory SDK — Anthropic Adapter
 *
 * Usage:
 *   import Anthropic from "@anthropic-ai/sdk";
 *   import { AnthropicMemoryAdapter } from "@stackowl/memory-sdk/adapters/anthropic";
 *
 *   const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
 *   const provider = new AnthropicMemoryAdapter(client);
 *   const sdk = new MemorySDK({ workspacePath: "./memory", provider });
 *
 * Note: Anthropic does not provide an embedding API.
 * The SDK falls back to keyword-only search when no embeddings are available.
 * For embeddings, use a dedicated embedding provider alongside Claude.
 */

import type { MemoryProvider } from "../types.js";

interface AnthropicClient {
  messages: {
    create(params: {
      model: string;
      max_tokens: number;
      system?: string;
      messages: Array<{ role: "user" | "assistant"; content: string }>;
      temperature?: number;
    }): Promise<{
      content: Array<{ type: string; text?: string }>;
    }>;
  };
}

export class AnthropicMemoryAdapter implements MemoryProvider {
  private client: AnthropicClient;
  private model: string;

  constructor(
    client: AnthropicClient,
    options: { model?: string } = {},
  ) {
    this.client = client;
    this.model = options.model ?? "claude-haiku-4-5-20251001";
  }

  async chat(
    messages: Array<{ role: "system" | "user" | "assistant"; content: string }>,
    options?: { maxTokens?: number; temperature?: number },
  ): Promise<{ content: string }> {
    // Anthropic: system message is separate, non-system messages must alternate user/assistant
    const systemMessage = messages.find((m) => m.role === "system")?.content;
    const chatMessages = messages
      .filter((m) => m.role !== "system")
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

    const response = await this.client.messages.create({
      model: this.model,
      max_tokens: options?.maxTokens ?? 1024,
      system: systemMessage,
      messages: chatMessages,
      temperature: options?.temperature,
    });

    const textBlock = response.content.find((b) => b.type === "text");
    return { content: textBlock?.text ?? "" };
  }

  // Anthropic has no embedding API — SDK falls back to keyword search
}
