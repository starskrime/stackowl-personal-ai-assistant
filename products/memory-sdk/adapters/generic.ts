/**
 * Memory SDK — Generic Fetch Adapter
 *
 * For any OpenAI-compatible REST API (local models, proxies, etc.)
 *
 * Usage:
 *   const provider = new GenericMemoryAdapter({
 *     baseUrl: "http://localhost:11434/v1",
 *     apiKey: "ollama",
 *     model: "llama3.2",
 *   });
 */

import type { MemoryProvider } from "../types.js";

export interface GenericAdapterConfig {
  baseUrl: string;
  apiKey?: string;
  model: string;
  embedModel?: string;
}

export class GenericMemoryAdapter implements MemoryProvider {
  private config: GenericAdapterConfig;

  constructor(config: GenericAdapterConfig) {
    this.config = config;
  }

  async chat(
    messages: Array<{ role: "system" | "user" | "assistant"; content: string }>,
    options?: { maxTokens?: number; temperature?: number },
  ): Promise<{ content: string }> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.config.apiKey) {
      headers["Authorization"] = `Bearer ${this.config.apiKey}`;
    }

    const body: Record<string, unknown> = {
      model: this.config.model,
      messages,
    };
    if (options?.maxTokens) body["max_tokens"] = options.maxTokens;
    if (options?.temperature !== undefined) body["temperature"] = options.temperature;

    const response = await fetch(`${this.config.baseUrl}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      throw new Error(`Provider error: ${response.status} ${response.statusText}`);
    }

    const data = (await response.json()) as {
      choices: Array<{ message: { content: string } }>;
    };

    return { content: data.choices[0]?.message?.content ?? "" };
  }

  async embed(text: string): Promise<{ embedding: number[] }> {
    if (!this.config.embedModel) {
      return { embedding: [] };
    }

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.config.apiKey) {
      headers["Authorization"] = `Bearer ${this.config.apiKey}`;
    }

    const response = await fetch(`${this.config.baseUrl}/embeddings`, {
      method: "POST",
      headers,
      body: JSON.stringify({ model: this.config.embedModel, input: text }),
    });

    if (!response.ok) {
      return { embedding: [] };
    }

    const data = (await response.json()) as {
      data: Array<{ embedding: number[] }>;
    };

    return { embedding: data.data[0]?.embedding ?? [] };
  }
}
