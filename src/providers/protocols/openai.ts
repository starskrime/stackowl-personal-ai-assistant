/**
 * StackOwl — OpenAI Protocol Implementation
 *
 * Handles all providers with compatible: openai
 * (openai, ollama, minimax, lmstudio, and any custom OpenAI-compatible endpoint)
 *
 * Uses the official `openai` npm SDK.
 */

import OpenAI from "openai";
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
  TokenUsage,
} from "../base.js";

// ─── Message Conversion ─────────────────────────────────────────

type OAIMessage = OpenAI.Chat.Completions.ChatCompletionMessageParam;

function toOAIMessages(messages: ChatMessage[]): OAIMessage[] {
  const result: OAIMessage[] = [];

  for (const m of messages) {
    if (m.role === "tool") {
      result.push({
        role: "tool",
        tool_call_id: m.toolCallId ?? "unknown",
        content: m.content,
      });
      continue;
    }

    if (m.role === "assistant" && m.toolCalls?.length) {
      result.push({
        role: "assistant",
        content: m.content || null,
        tool_calls: m.toolCalls.map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: { name: tc.name, arguments: JSON.stringify(tc.arguments) },
        })),
      });
      continue;
    }

    result.push({ role: m.role as "system" | "user" | "assistant", content: m.content });
  }

  return result;
}

function toOAITools(
  tools: ToolDefinition[],
): OpenAI.Chat.Completions.ChatCompletionTool[] {
  return tools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: t.parameters as Record<string, unknown>,
    },
  }));
}

// ─── Provider ───────────────────────────────────────────────────

export class OpenAIProtocolProvider implements ModelProvider {
  readonly name: string;
  protected client: OpenAI;
  protected activeModel: string;
  private embeddingModel: string;

  constructor(config: ProviderConfig, baseUrl: string) {
    this.name = config.name;
    this.activeModel =
      (config as any).activeModel ?? config.defaultModel ?? "gpt-4o";
    this.embeddingModel =
      config.defaultEmbeddingModel ?? "text-embedding-3-small";

    this.client = new OpenAI({
      apiKey: config.apiKey ?? process.env.OPENAI_API_KEY ?? "not-set",
      baseURL: config.baseUrl ?? baseUrl,
    });
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const completion = await this.client.chat.completions.create({
      model: model ?? this.activeModel,
      messages: toOAIMessages(messages),
      temperature: options?.temperature,
      max_tokens: options?.maxTokens ?? 8192,
      top_p: options?.topP,
      stop: options?.stop,
      stream: false,
    });

    const choice = completion.choices[0];
    return {
      content: choice.message.content ?? "",
      model: completion.model,
      finishReason: choice.finish_reason === "tool_calls" ? "tool_calls" : "stop",
      usage: completion.usage
        ? {
            promptTokens: completion.usage.prompt_tokens,
            completionTokens: completion.usage.completion_tokens,
            totalTokens: completion.usage.total_tokens,
          }
        : undefined,
    };
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const params: OpenAI.Chat.Completions.ChatCompletionCreateParamsNonStreaming =
      {
        model: model ?? this.activeModel,
        messages: toOAIMessages(messages),
        temperature: options?.temperature,
        max_tokens: options?.maxTokens ?? 8192,
        top_p: options?.topP,
        stream: false,
      };

    if (tools.length > 0) {
      params.tools = toOAITools(tools);
    }

    const completion = await this.client.chat.completions.create(params);
    const choice = completion.choices[0];

    const toolCalls: ToolCall[] = (choice.message.tool_calls ?? []).map(
      (tc: any) => ({
        id: tc.id,
        name: tc.function.name,
        arguments: JSON.parse(tc.function.arguments || "{}"),
      }),
    );

    return {
      content: choice.message.content ?? "",
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      model: completion.model,
      finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
      usage: completion.usage
        ? {
            promptTokens: completion.usage.prompt_tokens,
            completionTokens: completion.usage.completion_tokens,
            totalTokens: completion.usage.total_tokens,
          }
        : undefined,
    };
  }

  async *chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    const params: OpenAI.Chat.Completions.ChatCompletionCreateParamsStreaming =
      {
        model: model ?? this.activeModel,
        messages: toOAIMessages(messages),
        temperature: options?.temperature,
        max_tokens: options?.maxTokens ?? 8192,
        stream: true,
      };

    if (tools.length > 0) {
      params.tools = toOAITools(tools);
    }

    const stream = this.client.chat.completions.stream(params);

    // Track tool call accumulation across deltas
    const toolCallAccum = new Map<
      number,
      { id: string; name: string; argsStr: string }
    >();
    let usage: TokenUsage | undefined;

    for await (const chunk of stream) {
      const choice = chunk.choices?.[0];
      if (!choice) continue;

      const delta = choice.delta;
      if (!delta) continue;

      // Text content
      if (delta.content) {
        yield { type: "text_delta", content: delta.content };
      }

      // Tool call deltas
      if (delta.tool_calls) {
        for (const tcDelta of delta.tool_calls) {
          const idx = tcDelta.index ?? 0;

          if (!toolCallAccum.has(idx)) {
            const id =
              tcDelta.id ??
              `tc_${Date.now()}_${idx}_${Math.random().toString(36).substring(2, 8)}`;
            const name = tcDelta.function?.name ?? "";
            toolCallAccum.set(idx, { id, name, argsStr: "" });
            if (name) yield { type: "tool_start", toolCallId: id, toolName: name };
          }

          const accum = toolCallAccum.get(idx)!;

          if (tcDelta.id) accum.id = tcDelta.id;

          if (tcDelta.function?.name && !accum.name) {
            accum.name = tcDelta.function.name;
            yield { type: "tool_start", toolCallId: accum.id, toolName: accum.name };
          }

          if (tcDelta.function?.arguments) {
            accum.argsStr += tcDelta.function.arguments;
            yield {
              type: "tool_args_delta",
              toolCallId: accum.id,
              argsDelta: tcDelta.function.arguments,
            };
          }
        }
      }
    }

    // Emit tool_end for all accumulated tool calls
    for (const [, accum] of toolCallAccum) {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(accum.argsStr || "{}");
      } catch {
        args = {};
      }
      yield {
        type: "tool_end",
        toolCallId: accum.id,
        toolName: accum.name,
        arguments: args,
      };
    }

    // Get final completion for usage
    try {
      const final = await stream.finalChatCompletion();
      if (final.usage) {
        usage = {
          promptTokens: final.usage.prompt_tokens,
          completionTokens: final.usage.completion_tokens,
          totalTokens: final.usage.total_tokens,
        };
      }
    } catch {
      // usage optional
    }

    yield { type: "done", usage };
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const stream = await this.client.chat.completions.create({
      model: model ?? this.activeModel,
      messages: toOAIMessages(messages),
      temperature: options?.temperature,
      max_tokens: options?.maxTokens,
      stream: true,
    });

    for await (const chunk of stream) {
      const content = chunk.choices[0]?.delta?.content ?? "";
      yield { content, done: false };
    }

    yield { content: "", done: true };
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    const response = await this.client.embeddings.create({
      model: model ?? this.embeddingModel,
      input: text,
    });
    return {
      embedding: response.data[0].embedding,
      model: response.model,
    };
  }

  async listModels(): Promise<string[]> {
    try {
      const response = await this.client.models.list();
      return response.data.map((m) => m.id);
    } catch {
      return [this.activeModel];
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.client.models.list();
      return true;
    } catch (err) {
      // API errors (4xx/5xx) mean the server IS reachable — key may be invalid
      // but the endpoint is up. Only network-level errors mean unreachable.
      if (err instanceof Error) {
        const msg = err.message;
        const isNetworkError =
          msg.includes("ECONNREFUSED") ||
          msg.includes("ENOTFOUND") ||
          msg.includes("ETIMEDOUT") ||
          msg.includes("timeout") ||
          msg.includes("network") ||
          msg.includes("fetch failed");
        if (!isNetworkError) return true;
      }
      return false;
    }
  }
}
