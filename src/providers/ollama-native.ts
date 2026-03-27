/**
 * StackOwl — Native Ollama Provider
 *
 * Connects directly to Ollama via the official `ollama` npm package.
 * No LangChain overhead — full access to streaming, tool calling, and embeddings.
 */

import { Ollama } from "ollama";
import type {
  ModelProvider,
  ChatMessage,
  ChatResponse,
  ChatOptions,
  ToolDefinition,
  ToolCall,
  StreamChunk,
  StreamEvent,
  EmbeddingResponse,
  ProviderConfig,
} from "./base.js";
import { log } from "../logger.js";

// ─── Message Conversion ─────────────────────────────────────────

interface OllamaMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: Array<{
    function: { name: string; arguments: Record<string, unknown> };
  }>;
}

function toOllamaMessages(messages: ChatMessage[]): OllamaMessage[] {
  return messages.map((m) => {
    const msg: OllamaMessage = {
      role: m.role,
      content: m.content,
    };
    if (m.toolCalls && m.toolCalls.length > 0) {
      msg.tool_calls = m.toolCalls.map((tc) => ({
        function: {
          name: tc.name,
          arguments: tc.arguments,
        },
      }));
    }
    return msg;
  });
}

function toOllamaTools(tools: ToolDefinition[]): Array<{
  type: "function";
  function: { name: string; description: string; parameters: unknown };
}> {
  return tools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.parameters,
    },
  }));
}

function extractToolCalls(message: any): ToolCall[] {
  if (!message?.tool_calls?.length) return [];
  return message.tool_calls.map((tc: any, i: number) => ({
    id:
      tc.id ??
      `tc_${Date.now()}_${i}_${Math.random().toString(36).substring(2, 8)}`,
    name: tc.function?.name ?? "unknown",
    arguments: tc.function?.arguments ?? {},
  }));
}

// ─── Provider ───────────────────────────────────────────────────

export class OllamaNativeProvider implements ModelProvider {
  readonly name = "ollama";
  private client: Ollama;
  private defaultModel: string;
  private defaultEmbeddingModel: string;
  private baseUrl: string;

  constructor(config: ProviderConfig) {
    this.baseUrl = config.baseUrl ?? "http://127.0.0.1:11434";
    this.defaultModel = config.defaultModel ?? "llama3.2";
    this.defaultEmbeddingModel =
      config.defaultEmbeddingModel ?? "nomic-embed-text";
    this.client = new Ollama({ host: this.baseUrl });
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const response = await this.client.chat({
      model: model ?? this.defaultModel,
      messages: toOllamaMessages(messages) as any,
      stream: false,
      options: {
        temperature: options?.temperature,
        top_p: options?.topP,
        num_predict: options?.maxTokens,
      },
    });

    return {
      content: response.message?.content ?? "",
      model: model ?? this.defaultModel,
      finishReason: "stop",
      usage: {
        promptTokens: response.prompt_eval_count ?? 0,
        completionTokens: response.eval_count ?? 0,
        totalTokens:
          (response.prompt_eval_count ?? 0) + (response.eval_count ?? 0),
      },
    };
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    try {
      const response = await this.client.chat({
        model: model ?? this.defaultModel,
        messages: toOllamaMessages(messages) as any,
        tools: toOllamaTools(tools) as any,
        stream: false,
        options: {
          temperature: options?.temperature,
          top_p: options?.topP,
          num_predict: options?.maxTokens,
        },
      });

      const toolCalls = extractToolCalls(response.message);

      return {
        content: response.message?.content ?? "",
        toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
        model: model ?? this.defaultModel,
        finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
        usage: {
          promptTokens: response.prompt_eval_count ?? 0,
          completionTokens: response.eval_count ?? 0,
          totalTokens:
            (response.prompt_eval_count ?? 0) + (response.eval_count ?? 0),
        },
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Ollama's Go backend fails to parse malformed tool call JSON from the model.
      // Retry without tools so the model responds with plain text instead of crashing.
      if (
        msg.includes("error parsing tool call") ||
        msg.includes("invalid character")
      ) {
        log.engine.warn(
          `[Ollama] Tool call parse error — retrying without tools: ${msg.slice(0, 120)}`,
        );
        return this.chat(messages, model, options);
      }
      throw err;
    }
  }

  async *chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    let stream: any;
    try {
      stream = await this.client.chat({
        model: model ?? this.defaultModel,
        messages: toOllamaMessages(messages) as any,
        tools: toOllamaTools(tools) as any,
        stream: true,
        options: {
          temperature: options?.temperature,
          top_p: options?.topP,
          num_predict: options?.maxTokens,
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (
        msg.includes("error parsing tool call") ||
        msg.includes("invalid character")
      ) {
        log.engine.warn(
          `[Ollama] Tool call parse error in stream — falling back to plain chat: ${msg.slice(0, 120)}`,
        );
        // Fall back to non-tool streaming, converting StreamChunk → StreamEvent
        for await (const chunk of this.chatStream(messages, model, options)) {
          if (chunk.content) {
            yield { type: "text_delta" as const, content: chunk.content };
          }
          if (chunk.done) {
            yield { type: "done" as const, usage: undefined };
          }
        }
        return;
      }
      throw err;
    }

    // Ollama streams text deltas; tool calls arrive in the final chunk
    // We accumulate tool calls and emit them at the end
    let lastChunk: any = null;

    for await (const chunk of stream as any) {
      lastChunk = chunk;

      // Text delta
      if (chunk.message?.content) {
        yield { type: "text_delta", content: chunk.message.content };
      }
    }

    // Emit tool calls from the final accumulated state
    if (lastChunk?.message?.tool_calls?.length) {
      for (const tc of lastChunk.message.tool_calls) {
        const id = `tc_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`;
        const name = tc.function?.name ?? "unknown";
        const args = tc.function?.arguments ?? {};

        yield { type: "tool_start", toolCallId: id, toolName: name };
        yield {
          type: "tool_end",
          toolCallId: id,
          toolName: name,
          arguments: args,
        };
      }
    }

    yield {
      type: "done",
      usage: lastChunk
        ? {
            promptTokens: lastChunk.prompt_eval_count ?? 0,
            completionTokens: lastChunk.eval_count ?? 0,
            totalTokens:
              (lastChunk.prompt_eval_count ?? 0) + (lastChunk.eval_count ?? 0),
          }
        : undefined,
    };
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const stream = await this.client.chat({
      model: model ?? this.defaultModel,
      messages: toOllamaMessages(messages) as any,
      stream: true,
      options: {
        temperature: options?.temperature,
        top_p: options?.topP,
        num_predict: options?.maxTokens,
      },
    });

    for await (const chunk of stream as any) {
      yield {
        content: chunk.message?.content ?? "",
        done: chunk.done ?? false,
      };
    }
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    const response = await this.client.embed({
      model: model ?? this.defaultEmbeddingModel,
      input: text,
    });

    return {
      embedding: response.embeddings?.[0] ?? [],
      model: model ?? this.defaultEmbeddingModel,
    };
  }

  async listModels(): Promise<string[]> {
    const response = await this.client.list();
    return response.models?.map((m: any) => m.name) ?? [];
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/tags`, {
        method: "GET",
        signal: AbortSignal.timeout(5000),
      });
      return res.ok;
    } catch {
      return false;
    }
  }
}
