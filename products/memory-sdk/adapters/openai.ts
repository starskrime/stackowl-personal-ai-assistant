/**
 * Memory SDK — OpenAI Adapter
 *
 * Usage:
 *   import OpenAI from "openai";
 *   import { OpenAIMemoryAdapter } from "@stackowl/memory-sdk/adapters/openai";
 *
 *   const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
 *   const provider = new OpenAIMemoryAdapter(client);
 *   const sdk = new MemorySDK({ workspacePath: "./memory", provider });
 */

import type { MemoryProvider } from "../types.js";

interface OpenAIClient {
  chat: {
    completions: {
      create(params: {
        model: string;
        messages: Array<{ role: string; content: string }>;
        max_tokens?: number;
        temperature?: number;
      }): Promise<{
        choices: Array<{ message: { content: string | null } }>;
      }>;
    };
  };
  embeddings: {
    create(params: {
      model: string;
      input: string;
    }): Promise<{
      data: Array<{ embedding: number[] }>;
    }>;
  };
}

export class OpenAIMemoryAdapter implements MemoryProvider {
  private client: OpenAIClient;
  private chatModel: string;
  private embedModel: string;

  constructor(
    client: OpenAIClient,
    options: { chatModel?: string; embedModel?: string } = {},
  ) {
    this.client = client;
    this.chatModel = options.chatModel ?? "gpt-4o-mini";
    this.embedModel = options.embedModel ?? "text-embedding-3-small";
  }

  async chat(
    messages: Array<{ role: "system" | "user" | "assistant"; content: string }>,
    options?: { maxTokens?: number; temperature?: number },
  ): Promise<{ content: string }> {
    const response = await this.client.chat.completions.create({
      model: this.chatModel,
      messages,
      max_tokens: options?.maxTokens,
      temperature: options?.temperature,
    });
    return { content: response.choices[0]?.message?.content ?? "" };
  }

  async embed(text: string): Promise<{ embedding: number[] }> {
    const response = await this.client.embeddings.create({
      model: this.embedModel,
      input: text,
    });
    return { embedding: response.data[0]?.embedding ?? [] };
  }
}
