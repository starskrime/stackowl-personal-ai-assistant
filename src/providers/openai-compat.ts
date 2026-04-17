/**
 * StackOwl — OpenAI-Compatible Provider
 *
 * Works with any endpoint that implements the OpenAI /v1/chat/completions API:
 * OpenRouter, Together, LMStudio, vLLM, Groq, DeepSeek, etc.
 *
 * Uses native fetch — no external dependencies.
 */

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
} from "./base.js";

// ─── Message Conversion ─────────────────────────────────────────

interface OpenAIMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
}

function toOpenAIMessages(messages: ChatMessage[]): OpenAIMessage[] {
  return messages.map((m) => {
    const msg: OpenAIMessage = {
      role: m.role,
      content: m.content,
    };

    if (m.role === "tool" && m.toolCallId) {
      msg.tool_call_id = m.toolCallId;
      if (m.name) msg.name = m.name;
    }

    if (m.toolCalls && m.toolCalls.length > 0) {
      msg.tool_calls = m.toolCalls.map((tc) => ({
        id: tc.id,
        type: "function" as const,
        function: {
          name: tc.name,
          arguments: JSON.stringify(tc.arguments),
        },
      }));
    }

    return msg;
  });
}

function toOpenAITools(tools: ToolDefinition[]): Array<{
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

// ─── SSE Parser ─────────────────────────────────────────────────

async function* parseSSE(
  response: Response,
): AsyncGenerator<Record<string, unknown>> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(":")) continue;
        if (trimmed === "data: [DONE]") return;
        if (trimmed.startsWith("data: ")) {
          try {
            yield JSON.parse(trimmed.slice(6));
          } catch {
            // skip malformed JSON
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ─── Provider ───────────────────────────────────────────────────

export class OpenAICompatProvider implements ModelProvider {
  readonly name: string;
  private baseUrl: string;
  private apiKey: string;
  private defaultModel: string;
  private defaultEmbeddingModel: string;

  constructor(config: ProviderConfig) {
    this.name = config.name ?? "openai-compatible";
    this.baseUrl = (config.baseUrl ?? "http://127.0.0.1:1234").replace(
      /\/+$/,
      "",
    );
    this.apiKey = config.apiKey ?? "";
    this.defaultModel = config.defaultModel ?? "default";
    this.defaultEmbeddingModel = config.defaultEmbeddingModel ?? "default";
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.apiKey) {
      h["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return h;
  }

  private static readonly TIMEOUT_MS = 60_000;

  private completionsUrl(): string {
    // Support both /v1/chat/completions and bare base URLs
    if (this.baseUrl.endsWith("/chat/completions")) return this.baseUrl;
    if (this.baseUrl.endsWith("/v1")) return `${this.baseUrl}/chat/completions`;
    return `${this.baseUrl}/v1/chat/completions`;
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: toOpenAIMessages(messages),
      stream: false,
    };
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;
    if (options?.maxTokens !== undefined) body.max_tokens = options.maxTokens;
    else body.max_tokens = 8192;
    if (options?.topP !== undefined) body.top_p = options.topP;
    if (options?.stop) body.stop = options.stop;

    const res = await fetch(this.completionsUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(OpenAICompatProvider.TIMEOUT_MS),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `[${this.name}] Chat failed: HTTP ${res.status} ${text.slice(0, 200)}`,
      );
    }

    const data = (await res.json()) as any;
    const choice = data.choices?.[0];
    const usage = data.usage;

    return {
      content: choice?.message?.content ?? "",
      model: data.model ?? model ?? this.defaultModel,
      finishReason:
        choice?.finish_reason === "tool_calls" ? "tool_calls" : "stop",
      usage: usage
        ? {
            promptTokens: usage.prompt_tokens ?? 0,
            completionTokens: usage.completion_tokens ?? 0,
            totalTokens: usage.total_tokens ?? 0,
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
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: toOpenAIMessages(messages),
      stream: false,
    };
    if (tools.length > 0) {
      body.tools = toOpenAITools(tools);
    }
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;
    if (options?.maxTokens !== undefined) body.max_tokens = options.maxTokens;
    else body.max_tokens = 8192; // Cap response length to prevent runaway responses
    if (options?.topP !== undefined) body.top_p = options.topP;

    const res = await fetch(this.completionsUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(OpenAICompatProvider.TIMEOUT_MS),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `[${this.name}] ChatWithTools failed: HTTP ${res.status} ${text.slice(0, 200)}`,
      );
    }

    const data = (await res.json()) as any;
    const choice = data.choices?.[0];
    const usage = data.usage;

    const toolCalls: ToolCall[] = [];
    if (choice?.message?.tool_calls?.length) {
      for (const tc of choice.message.tool_calls) {
        let args: Record<string, unknown> = {};
        const rawArgs = tc.function?.arguments ?? "{}";
        try {
          args = JSON.parse(rawArgs);
        } catch {
          const preview = rawArgs.slice(0, 100);
          throw new Error(
            `[${this.name}] Malformed tool call arguments for ${tc.function?.name}: ${preview}${rawArgs.length > 100 ? "..." : ""}`,
          );
        }
        toolCalls.push({
          id:
            tc.id ??
            `tc_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
          name: tc.function?.name ?? "unknown",
          arguments: args,
        });
      }
    }

    return {
      content: choice?.message?.content ?? "",
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      model: data.model ?? model ?? this.defaultModel,
      finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
      usage: usage
        ? {
            promptTokens: usage.prompt_tokens ?? 0,
            completionTokens: usage.completion_tokens ?? 0,
            totalTokens: usage.total_tokens ?? 0,
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
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: toOpenAIMessages(messages),
      stream: true,
    };
    if (tools.length > 0) {
      body.tools = toOpenAITools(tools);
    }
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;
    if (options?.maxTokens !== undefined) body.max_tokens = options.maxTokens;
    else body.max_tokens = 8192; // Cap streaming responses too

    const res = await fetch(this.completionsUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(OpenAICompatProvider.TIMEOUT_MS),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `[${this.name}] Stream failed: HTTP ${res.status} ${text.slice(0, 200)}`,
      );
    }

    // Track tool call accumulation across deltas
    const toolCallAccum: Map<
      number,
      { id: string; name: string; argsStr: string }
    > = new Map();
    let usage: TokenUsage | undefined;

    try {
      for await (const chunk of parseSSE(res)) {
        const choice = (chunk as any).choices?.[0];
        if (!choice) {
          // Check for usage in final chunk
          if ((chunk as any).usage) {
            const u = (chunk as any).usage;
            usage = {
              promptTokens: u.prompt_tokens ?? 0,
              completionTokens: u.completion_tokens ?? 0,
              totalTokens: u.total_tokens ?? 0,
            };
          }
          continue;
        }

        const delta = choice.delta;
        if (!delta) continue;

        // Text content delta
        if (delta.content) {
          yield { type: "text_delta", content: delta.content };
        }

        // Tool call deltas — OpenAI streams these incrementally by index
        if (delta.tool_calls) {
          for (const tcDelta of delta.tool_calls) {
            const idx = tcDelta.index ?? 0;

            if (!toolCallAccum.has(idx)) {
              // First delta for this tool call — emit start
              const id =
                tcDelta.id ??
                `tc_${Date.now()}_${idx}_${Math.random().toString(36).substring(2, 8)}`;
              const name = tcDelta.function?.name ?? "";
              toolCallAccum.set(idx, { id, name, argsStr: "" });

              if (name) {
                yield { type: "tool_start", toolCallId: id, toolName: name };
              }
            }

            const accum = toolCallAccum.get(idx)!;

            // Update name if we got it later
            if (tcDelta.function?.name && !accum.name) {
              accum.name = tcDelta.function.name;
              yield {
                type: "tool_start",
                toolCallId: accum.id,
                toolName: accum.name,
              };
            }

            // Update ID if we got it later
            if (tcDelta.id) {
              accum.id = tcDelta.id;
            }

            // Accumulate argument string chunks
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

        // Check for usage
        if ((chunk as any).usage) {
          const u = (chunk as any).usage;
          usage = {
            promptTokens: u.prompt_tokens ?? 0,
            completionTokens: u.completion_tokens ?? 0,
            totalTokens: u.total_tokens ?? 0,
          };
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      yield { type: "text_delta", content: `\n[Stream error: ${msg}]\n` };
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

    yield { type: "done", usage };
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: toOpenAIMessages(messages),
      stream: true,
    };
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;
    if (options?.maxTokens !== undefined) body.max_tokens = options.maxTokens;

    const res = await fetch(this.completionsUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(OpenAICompatProvider.TIMEOUT_MS),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`[${this.name}] Stream failed: HTTP ${res.status}${text ? " — " + text.slice(0, 200) : ""}`);
    }

    for await (const chunk of parseSSE(res)) {
      const delta = (chunk as any).choices?.[0]?.delta;
      yield {
        content: delta?.content ?? "",
        done: false,
      };
    }

    yield { content: "", done: true };
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    const embeddingsUrl = this.baseUrl.endsWith("/v1")
      ? `${this.baseUrl}/embeddings`
      : `${this.baseUrl}/v1/embeddings`;

    const res = await fetch(embeddingsUrl, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({
        model: model ?? this.defaultEmbeddingModel,
        input: text,
      }),
      signal: AbortSignal.timeout(30_000),
    });

    if (!res.ok) {
      throw new Error(`[${this.name}] Embed failed: HTTP ${res.status}`);
    }

    const data = (await res.json()) as any;
    return {
      embedding: data.data?.[0]?.embedding ?? [],
      model: model ?? this.defaultEmbeddingModel,
    };
  }

  async listModels(): Promise<string[]> {
    const modelsUrl = this.baseUrl.endsWith("/v1")
      ? `${this.baseUrl}/models`
      : `${this.baseUrl}/v1/models`;

    try {
      const res = await fetch(modelsUrl, {
        headers: this.headers(),
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) return [this.defaultModel];
      const data = (await res.json()) as any;
      return data.data?.map((m: any) => m.id) ?? [this.defaultModel];
    } catch {
      return [this.defaultModel];
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      const modelsUrl = this.baseUrl.endsWith("/v1")
        ? `${this.baseUrl}/models`
        : `${this.baseUrl}/v1/models`;
      const res = await fetch(modelsUrl, {
        headers: this.headers(),
        signal: AbortSignal.timeout(5000),
      });
      return res.ok;
    } catch {
      return false;
    }
  }
}
