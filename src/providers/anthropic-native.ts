/**
 * StackOwl — Native Anthropic Provider
 *
 * Connects directly to Anthropic's Claude API via `@anthropic-ai/sdk`.
 * Full access to streaming tool calls, prompt caching, and extended thinking.
 */

import Anthropic from "@anthropic-ai/sdk";
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

type AnthropicRole = "user" | "assistant";

interface AnthropicMessage {
  role: AnthropicRole;
  content:
    | string
    | Array<
        | { type: "text"; text: string }
        | {
            type: "tool_use";
            id: string;
            name: string;
            input: Record<string, unknown>;
          }
        | { type: "tool_result"; tool_use_id: string; content: string }
      >;
}

function toAnthropicMessages(messages: ChatMessage[]): {
  system: string;
  messages: AnthropicMessage[];
} {
  let system = "";
  const result: AnthropicMessage[] = [];

  for (const m of messages) {
    if (m.role === "system") {
      // Anthropic uses a top-level system parameter
      system += (system ? "\n\n" : "") + m.content;
      continue;
    }

    if (m.role === "tool") {
      // Tool results go into the user turn as tool_result content blocks
      result.push({
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: m.toolCallId ?? "unknown",
            content: m.content,
          },
        ],
      });
      continue;
    }

    if (m.role === "assistant" && m.toolCalls?.length) {
      // Assistant message with tool calls
      const contentBlocks: AnthropicMessage["content"] = [];
      if (m.content) {
        contentBlocks.push({ type: "text", text: m.content });
      }
      for (const tc of m.toolCalls) {
        contentBlocks.push({
          type: "tool_use",
          id: tc.id,
          name: tc.name,
          input: tc.arguments,
        });
      }
      result.push({ role: "assistant", content: contentBlocks });
      continue;
    }

    // Regular user or assistant message
    const role: AnthropicRole = m.role === "user" ? "user" : "assistant";

    // Anthropic requires strict alternation. If we have two consecutive
    // messages with the same role, merge them.
    if (result.length > 0 && result[result.length - 1].role === role) {
      const prev = result[result.length - 1];
      if (typeof prev.content === "string") {
        prev.content = prev.content + "\n\n" + m.content;
      } else {
        prev.content.push({ type: "text", text: m.content });
      }
    } else {
      result.push({ role, content: m.content });
    }
  }

  // Ensure messages start with "user" role (Anthropic requirement)
  if (result.length > 0 && result[0].role === "assistant") {
    result.unshift({ role: "user", content: "(continuing conversation)" });
  }

  // Ensure messages don't end with "user" followed by nothing
  // and that we have at least one message
  if (result.length === 0) {
    result.push({ role: "user", content: "(empty)" });
  }

  return { system, messages: result };
}

function toAnthropicTools(tools: ToolDefinition[]): Array<{
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}> {
  return tools.map((t) => ({
    name: t.name,
    description: t.description,
    input_schema: t.parameters as Record<string, unknown>,
  }));
}

// ─── Provider ───────────────────────────────────────────────────

export class AnthropicNativeProvider implements ModelProvider {
  readonly name = "anthropic";
  private client: Anthropic;
  private defaultModel: string;

  constructor(config: ProviderConfig) {
    this.defaultModel = config.defaultModel ?? "claude-sonnet-4-20250514";
    this.client = new Anthropic({
      apiKey: config.apiKey ?? process.env.ANTHROPIC_API_KEY,
      baseURL: config.baseUrl,
    });
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const { system, messages: anthropicMessages } =
      toAnthropicMessages(messages);

    const response = await this.client.messages.create({
      model: model ?? this.defaultModel,
      max_tokens: options?.maxTokens ?? 8192,
      system: system || undefined,
      messages: anthropicMessages as any,
      temperature: options?.temperature,
      top_p: options?.topP,
    });

    const textContent = response.content
      .filter((b: any) => b.type === "text")
      .map((b: any) => b.text)
      .join("");

    return {
      content: textContent,
      model: response.model,
      finishReason: response.stop_reason === "tool_use" ? "tool_calls" : "stop",
      usage: {
        promptTokens: response.usage.input_tokens,
        completionTokens: response.usage.output_tokens,
        totalTokens: response.usage.input_tokens + response.usage.output_tokens,
      },
    };
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const { system, messages: anthropicMessages } =
      toAnthropicMessages(messages);

    const params: any = {
      model: model ?? this.defaultModel,
      max_tokens: options?.maxTokens ?? 8192,
      system: system || undefined,
      messages: anthropicMessages,
      temperature: options?.temperature,
      top_p: options?.topP,
    };

    if (tools.length > 0) {
      params.tools = toAnthropicTools(tools);
    }

    const response = await this.client.messages.create(params);

    const textContent = response.content
      .filter((b: any) => b.type === "text")
      .map((b: any) => b.text)
      .join("");

    const toolCalls: ToolCall[] = response.content
      .filter((b: any) => b.type === "tool_use")
      .map((b: any) => ({
        id: b.id,
        name: b.name,
        arguments: b.input as Record<string, unknown>,
      }));

    return {
      content: textContent,
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      model: response.model,
      finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
      usage: {
        promptTokens: response.usage.input_tokens,
        completionTokens: response.usage.output_tokens,
        totalTokens: response.usage.input_tokens + response.usage.output_tokens,
      },
    };
  }

  async *chatWithToolsStream(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent> {
    const { system, messages: anthropicMessages } =
      toAnthropicMessages(messages);

    const params: any = {
      model: model ?? this.defaultModel,
      max_tokens: options?.maxTokens ?? 8192,
      system: system || undefined,
      messages: anthropicMessages,
      temperature: options?.temperature,
      top_p: options?.topP,
      stream: true,
    };

    if (tools.length > 0) {
      params.tools = toAnthropicTools(tools);
    }

    const stream = this.client.messages.stream(params);

    // Track current tool call being built
    let currentToolId = "";
    let currentToolName = "";
    let currentToolArgs = "";
    let usage: TokenUsage | undefined;

    for await (const event of stream) {
      switch (event.type) {
        case "content_block_start": {
          const block = (event as any).content_block;
          if (block?.type === "tool_use") {
            currentToolId = block.id;
            currentToolName = block.name;
            currentToolArgs = "";
            yield {
              type: "tool_start",
              toolCallId: currentToolId,
              toolName: currentToolName,
            };
          }
          break;
        }

        case "content_block_delta": {
          const delta = (event as any).delta;
          if (delta?.type === "text_delta" && delta.text) {
            yield { type: "text_delta", content: delta.text };
          } else if (delta?.type === "input_json_delta" && delta.partial_json) {
            currentToolArgs += delta.partial_json;
            yield {
              type: "tool_args_delta",
              toolCallId: currentToolId,
              argsDelta: delta.partial_json,
            };
          }
          break;
        }

        case "content_block_stop": {
          if (currentToolId && currentToolName) {
            let args: Record<string, unknown> = {};
            try {
              args = JSON.parse(currentToolArgs || "{}");
            } catch {
              args = {};
            }
            yield {
              type: "tool_end",
              toolCallId: currentToolId,
              toolName: currentToolName,
              arguments: args,
            };
            currentToolId = "";
            currentToolName = "";
            currentToolArgs = "";
          }
          break;
        }

        case "message_delta": {
          const messageDelta = event as any;
          if (messageDelta.usage) {
            usage = {
              promptTokens: 0, // set from message_start
              completionTokens: messageDelta.usage.output_tokens ?? 0,
              totalTokens: messageDelta.usage.output_tokens ?? 0,
            };
          }
          break;
        }

        case "message_start": {
          const messageStart = event as any;
          if (messageStart.message?.usage) {
            const u = messageStart.message.usage;
            usage = {
              promptTokens: u.input_tokens ?? 0,
              completionTokens: u.output_tokens ?? 0,
              totalTokens: (u.input_tokens ?? 0) + (u.output_tokens ?? 0),
            };
          }
          break;
        }
      }
    }

    yield { type: "done", usage };
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const { system, messages: anthropicMessages } =
      toAnthropicMessages(messages);

    const stream = this.client.messages.stream({
      model: model ?? this.defaultModel,
      max_tokens: options?.maxTokens ?? 8192,
      system: system || undefined,
      messages: anthropicMessages as any,
      temperature: options?.temperature,
    });

    for await (const event of stream) {
      if (event.type === "content_block_delta") {
        const delta = (event as any).delta;
        if (delta?.type === "text_delta") {
          yield { content: delta.text ?? "", done: false };
        }
      }
    }

    yield { content: "", done: true };
  }

  async embed(_text: string, _model?: string): Promise<EmbeddingResponse> {
    throw new Error(
      `[anthropic] Anthropic does not provide an embedding API. ` +
        `Use Ollama or an OpenAI-compatible provider for embeddings.`,
    );
  }

  async listModels(): Promise<string[]> {
    return [
      "claude-opus-4-20250514",
      "claude-sonnet-4-20250514",
      "claude-haiku-4-20250414",
    ];
  }

  async healthCheck(): Promise<boolean> {
    try {
      // Use models list — lightweight, no token cost
      await this.client.models.list();
      return true;
    } catch (err) {
      // API errors (auth, etc.) = server reachable; only network errors = unreachable
      if (err instanceof Error) {
        const msg = err.message;
        const isNetworkError =
          msg.includes("ECONNREFUSED") ||
          msg.includes("ENOTFOUND") ||
          msg.includes("ETIMEDOUT") ||
          msg.includes("timeout") ||
          msg.includes("fetch failed");
        if (!isNetworkError) return true;
      }
      return false;
    }
  }
}
