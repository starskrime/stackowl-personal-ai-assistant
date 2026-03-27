/**
 * StackOwl — MiniMax Provider (Anthropic-Compatible)
 *
 * MiniMax.io API using the Anthropic SDK compatibility layer.
 * Base URL: https://api.minimax.io/anthropic
 * Model: MiniMax-M2.7
 *
 * Uses native fetch — no external SDK dependency.
 * Auth: x-api-key header (Anthropic-compatible).
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

function toAnthropicMessages(messages: ChatMessage[]): {
  system: string;
  bodyMessages: Array<{ role: string; content: string | object[] }>;
} {
  let system = "";
  const bodyMessages: Array<{ role: string; content: string | object[] }> = [];

  for (const m of messages) {
    if (m.role === "system") {
      system = m.content;
      continue;
    }
    if (m.role === "tool") {
      bodyMessages.push({
        role: "user",
        content: [
          {
            type: "tool_result" as const,
            tool_use_id: m.toolCallId ?? "tool_use_id",
            content: m.content || "",
          },
        ],
      });
      continue;
    }
    if (m.role === "assistant" && m.toolCalls && m.toolCalls.length > 0) {
      const content: object[] = [];
      if (m.content) content.push({ type: "text" as const, text: m.content });
      for (const tc of m.toolCalls) {
        content.push({
          type: "tool_use" as const,
          id: tc.id,
          name: tc.name,
          input: tc.arguments,
        });
      }
      bodyMessages.push({ role: "assistant", content });
      continue;
    }
    if (m.role === "assistant" || m.role === "user") {
      bodyMessages.push({ role: m.role, content: m.content });
    }
  }
  return { system, bodyMessages };
}

async function* parseAnthropicSSE(
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
        if (trimmed === "event: message_stop") return;
        if (!trimmed) continue;
        if (trimmed.startsWith("data: ")) {
          try {
            yield JSON.parse(trimmed.slice(6));
          } catch {
            // skip
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export class MiniMaxProvider implements ModelProvider {
  readonly name: string;
  private baseUrl: string;
  private apiKey: string;
  private defaultModel: string;

  constructor(config: ProviderConfig) {
    this.name = config.name ?? "minimax";
    this.baseUrl = (
      config.baseUrl ?? "https://api.minimax.io/anthropic"
    ).replace(/\/+$/, "");
    this.apiKey = config.apiKey ?? "";
    this.defaultModel = config.defaultModel ?? "MiniMax-M2.7";
  }

  private headers(): Record<string, string> {
    return {
      "Content-Type": "application/json",
      "x-api-key": this.apiKey,
      "anthropic-version": "2023-06-01",
    };
  }

  private messagesUrl(): string {
    return `${this.baseUrl}/v1/messages`;
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(this.messagesUrl(), {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify({
          model: this.defaultModel,
          messages: [{ role: "user", content: "ping" }],
          max_tokens: 2,
        }),
        signal: AbortSignal.timeout(10000),
      });
      return res.ok || res.status === 401;
    } catch {
      return false;
    }
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const { system, bodyMessages } = toAnthropicMessages(messages);
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: bodyMessages,
      max_tokens: options?.maxTokens ?? 8192,
    };
    if (system) body.system = system;
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;
    if (options?.topP !== undefined) body.top_p = options.topP;

    const res = await fetch(this.messagesUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `[${this.name}] Chat failed: HTTP ${res.status} ${text.slice(0, 200)}`,
      );
    }
    const data = (await res.json()) as any;
    const content = data.content?.[0];
    const textBlock = data.content?.find((c: any) => c.type === "text");
    return {
      content: textBlock?.text ?? content?.text ?? "",
      model: data.model ?? model ?? this.defaultModel,
      finishReason: data.stop_reason === "end_turn" ? "stop" : "stop",
      usage: data.usage
        ? {
            promptTokens: data.usage.input_tokens ?? 0,
            completionTokens: data.usage.output_tokens ?? 0,
            totalTokens:
              (data.usage.input_tokens ?? 0) + (data.usage.output_tokens ?? 0),
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
    const { system, bodyMessages } = toAnthropicMessages(messages);
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: bodyMessages,
      max_tokens: options?.maxTokens ?? 8192,
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.parameters,
      })),
    };
    if (system) body.system = system;
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;

    const res = await fetch(this.messagesUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(
        `[${this.name}] ChatWithTools failed: HTTP ${res.status} ${text.slice(0, 200)}`,
      );
    }

    const data = (await res.json()) as any;
    const textBlock = data.content?.find((c: any) => c.type === "text");
    const toolCalls: ToolCall[] = [];
    for (const tc of data.content?.filter((c: any) => c.type === "tool_use") ??
      []) {
      toolCalls.push({
        id: tc.id ?? `tc_${Date.now()}`,
        name: tc.name,
        arguments: tc.input ?? {},
      });
    }

    return {
      content: textBlock?.text ?? "",
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      model: data.model ?? model ?? this.defaultModel,
      finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
      usage: data.usage
        ? {
            promptTokens: data.usage.input_tokens ?? 0,
            completionTokens: data.usage.output_tokens ?? 0,
            totalTokens:
              (data.usage.input_tokens ?? 0) + (data.usage.output_tokens ?? 0),
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
    const { system, bodyMessages } = toAnthropicMessages(messages);
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: bodyMessages,
      max_tokens: options?.maxTokens ?? 8192,
      stream: true,
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        input_schema: t.parameters,
      })),
    };
    if (system) body.system = system;
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;

    const res = await fetch(this.messagesUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`[${this.name}] Stream failed: HTTP ${res.status}`);
    }

    let usage: TokenUsage | undefined;
    const toolAccum: Map<
      number,
      { id: string; name: string; argsStr: string }
    > = new Map();

    for await (const chunk of parseAnthropicSSE(res)) {
      const eventType = (chunk as any).type;

      if (eventType === "message_delta") {
        if ((chunk as any).usage) {
          const u = (chunk as any).usage;
          usage = {
            promptTokens: u.input_tokens ?? 0,
            completionTokens: u.output_tokens ?? 0,
            totalTokens: (u.input_tokens ?? 0) + (u.output_tokens ?? 0),
          };
        }
        if ((chunk as any).stop_reason) {
          yield { type: "done", usage };
        }
      }

      if (eventType === "content_block_delta") {
        const delta = (chunk as any).delta;
        if (delta?.type === "text_delta") {
          yield { type: "text_delta", content: delta.text ?? "" };
        }
        if (delta?.type === "thinking_delta") {
          // Skip — thinking content is internal model deliberation, not for the user.
          // MiniMax sends this as a separate delta type; we simply don't forward it.
        }
        if (delta?.type === "input_json_delta") {
          // MiniMax sends partial JSON args via input_json_delta
          const index = (chunk as any).index ?? 0;
          const partial = delta.partial_json ?? "";
          if (!toolAccum.has(index)) {
            toolAccum.set(index, { id: "", name: "", argsStr: "" });
          }
          toolAccum.get(index)!.argsStr += partial;
          yield {
            type: "tool_args_delta",
            toolCallId: toolAccum.get(index)!.id || `tc_${index}`,
            argsDelta: partial,
          };
        }
      }

      if (eventType === "content_block_start") {
        const block = (chunk as any).content_block;
        const index = (chunk as any).index ?? 0;
        if (block?.type === "tool_use") {
          toolAccum.set(index, {
            id: block.id ?? `tc_${index}`,
            name: block.name ?? "",
            argsStr: "",
          });
          yield {
            type: "tool_start",
            toolCallId: block.id ?? `tc_${index}`,
            toolName: block.name ?? "",
          };
        }
      }

      if (eventType === "content_block_stop") {
        const index = (chunk as any).index ?? 0;
        const accum = toolAccum.get(index);
        if (accum && accum.name) {
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
          toolAccum.delete(index);
        }
      }
    }
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const { system, bodyMessages } = toAnthropicMessages(messages);
    const body: Record<string, unknown> = {
      model: model ?? this.defaultModel,
      messages: bodyMessages,
      max_tokens: options?.maxTokens ?? 8192,
      stream: true,
    };
    if (system) body.system = system;
    if (options?.temperature !== undefined)
      body.temperature = options.temperature;

    const res = await fetch(this.messagesUrl(), {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`[${this.name}] Stream failed: HTTP ${res.status}`);
    }

    for await (const chunk of parseAnthropicSSE(res)) {
      const eventType = (chunk as any).type;
      if (eventType === "content_block_delta") {
        const delta = (chunk as any).delta;
        if (delta?.type === "text_delta") {
          yield { content: delta.text ?? "", done: false };
        }
      }
    }
    yield { content: "", done: true };
  }

  async embed(_text: string, _model?: string): Promise<EmbeddingResponse> {
    return { embedding: [], model: this.defaultModel };
  }

  async listModels(): Promise<string[]> {
    return [this.defaultModel];
  }
}
