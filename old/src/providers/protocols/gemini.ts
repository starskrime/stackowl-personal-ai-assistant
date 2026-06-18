/**
 * StackOwl — Gemini Protocol Implementation
 *
 * Handles providers with compatible: gemini
 * Uses the official @google/genai SDK.
 */

import { GoogleGenAI } from "@google/genai";
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
import type { ModelDefinition } from "../../models/loader.js";

// ─── Message Conversion ─────────────────────────────────────────

interface GeminiPart {
  text?: string;
  functionCall?: { name: string; args: Record<string, unknown> };
  functionResponse?: { name: string; response: { output: string } };
}

interface GeminiContent {
  role: "user" | "model";
  parts: GeminiPart[];
}

function toGeminiContents(messages: ChatMessage[]): {
  systemInstruction: string;
  contents: GeminiContent[];
} {
  let systemInstruction = "";
  const contents: GeminiContent[] = [];

  for (const m of messages) {
    if (m.role === "system") {
      systemInstruction += (systemInstruction ? "\n\n" : "") + m.content;
      continue;
    }

    if (m.role === "tool") {
      // Tool results go as user turn with functionResponse parts
      contents.push({
        role: "user",
        parts: [
          {
            functionResponse: {
              name: m.name ?? "unknown",
              response: { output: m.content },
            },
          },
        ],
      });
      continue;
    }

    if (m.role === "assistant") {
      const parts: GeminiPart[] = [];
      if (m.content) parts.push({ text: m.content });
      if (m.toolCalls?.length) {
        for (const tc of m.toolCalls) {
          parts.push({ functionCall: { name: tc.name, args: tc.arguments } });
        }
      }
      contents.push({ role: "model", parts });
      continue;
    }

    // user message
    const prev = contents[contents.length - 1];
    if (prev?.role === "user") {
      // Merge consecutive user messages
      prev.parts.push({ text: m.content });
    } else {
      contents.push({ role: "user", parts: [{ text: m.content }] });
    }
  }

  // Gemini requires at least one content entry
  if (contents.length === 0) {
    contents.push({ role: "user", parts: [{ text: "(empty)" }] });
  }

  return { systemInstruction, contents };
}

function toGeminiTools(tools: ToolDefinition[]) {
  if (tools.length === 0) return undefined;
  return [
    {
      functionDeclarations: tools.map((t) => ({
        name: t.name,
        description: t.description,
        parameters: t.parameters,
      })),
    },
  ];
}

function extractToolCalls(candidate: any): ToolCall[] {
  const toolCalls: ToolCall[] = [];
  const parts = candidate?.content?.parts ?? [];
  for (const part of parts) {
    if (part.functionCall) {
      toolCalls.push({
        id: `tc_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
        name: part.functionCall.name,
        arguments: part.functionCall.args ?? {},
      });
    }
  }
  return toolCalls;
}

function extractText(candidate: any): string {
  const parts = candidate?.content?.parts ?? [];
  return parts
    .filter((p: any) => p.text)
    .map((p: any) => p.text)
    .join("");
}

// ─── Provider ───────────────────────────────────────────────────

export class GeminiProtocolProvider implements ModelProvider {
  readonly name: string;
  private client: GoogleGenAI;
  private activeModel: string;

  constructor(config: ProviderConfig, modelDef: ModelDefinition) {
    this.name = config.name;
    this.activeModel =
      (config as any).activeModel ?? config.defaultModel ?? modelDef.defaultModel;

    this.client = new GoogleGenAI({
      apiKey: config.apiKey ?? process.env.GOOGLE_API_KEY ?? "",
    });
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const { systemInstruction, contents } = toGeminiContents(messages);
    const m = model ?? this.activeModel;

    const response = await this.client.models.generateContent({
      model: m,
      contents,
      config: {
        systemInstruction: systemInstruction || undefined,
        temperature: options?.temperature,
        maxOutputTokens: options?.maxTokens,
        topP: options?.topP,
      },
    });

    const candidate = response.candidates?.[0];
    const text = extractText(candidate);
    const usage = response.usageMetadata;

    return {
      content: text,
      model: m,
      finishReason: "stop",
      usage: usage
        ? {
            promptTokens: usage.promptTokenCount ?? 0,
            completionTokens: usage.candidatesTokenCount ?? 0,
            totalTokens: usage.totalTokenCount ?? 0,
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
    const { systemInstruction, contents } = toGeminiContents(messages);
    const m = model ?? this.activeModel;

    const response = await this.client.models.generateContent({
      model: m,
      contents,
      ...(toGeminiTools(tools) ? { tools: toGeminiTools(tools) as any } : {}),
      config: {
        systemInstruction: systemInstruction || undefined,
        temperature: options?.temperature,
        maxOutputTokens: options?.maxTokens,
        topP: options?.topP,
      },
    });

    const candidate = response.candidates?.[0];
    const text = extractText(candidate);
    const toolCalls = extractToolCalls(candidate);
    const usage = response.usageMetadata;

    return {
      content: text,
      toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
      model: m,
      finishReason: toolCalls.length > 0 ? "tool_calls" : "stop",
      usage: usage
        ? {
            promptTokens: usage.promptTokenCount ?? 0,
            completionTokens: usage.candidatesTokenCount ?? 0,
            totalTokens: usage.totalTokenCount ?? 0,
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
    const { systemInstruction, contents } = toGeminiContents(messages);
    const m = model ?? this.activeModel;

    const stream = this.client.models.generateContentStream({
      model: m,
      contents,
      ...(toGeminiTools(tools) ? { tools: toGeminiTools(tools) as any } : {}),
      config: {
        systemInstruction: systemInstruction || undefined,
        temperature: options?.temperature,
        maxOutputTokens: options?.maxTokens,
        topP: options?.topP,
      },
    });

    let usage: TokenUsage | undefined;
    const seenFunctionCalls: ToolCall[] = [];

    for await (const chunk of await stream) {
      const candidate = chunk.candidates?.[0];
      if (!candidate) continue;

      // Text deltas
      const text = extractText(candidate);
      if (text) yield { type: "text_delta", content: text };

      // Tool calls (Gemini delivers these as complete blocks, not deltas)
      const toolCalls = extractToolCalls(candidate);
      for (const tc of toolCalls) {
        seenFunctionCalls.push(tc);
        yield { type: "tool_start", toolCallId: tc.id, toolName: tc.name };
        const argsStr = JSON.stringify(tc.arguments);
        yield { type: "tool_args_delta", toolCallId: tc.id, argsDelta: argsStr };
        yield {
          type: "tool_end",
          toolCallId: tc.id,
          toolName: tc.name,
          arguments: tc.arguments,
        };
      }

      // Usage in final chunk
      const u = chunk.usageMetadata;
      if (u) {
        usage = {
          promptTokens: u.promptTokenCount ?? 0,
          completionTokens: u.candidatesTokenCount ?? 0,
          totalTokens: u.totalTokenCount ?? 0,
        };
      }
    }

    yield { type: "done", usage };
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const { systemInstruction, contents } = toGeminiContents(messages);
    const m = model ?? this.activeModel;

    const stream = this.client.models.generateContentStream({
      model: m,
      contents,
      config: {
        systemInstruction: systemInstruction || undefined,
        temperature: options?.temperature,
        maxOutputTokens: options?.maxTokens,
      },
    });

    for await (const chunk of await stream) {
      const text = extractText(chunk.candidates?.[0]);
      yield { content: text, done: false };
    }

    yield { content: "", done: true };
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    const m = model ?? "text-embedding-004";
    const response = await this.client.models.embedContent({
      model: m,
      contents: [{ role: "user", parts: [{ text }] }],
    });
    return {
      embedding: response.embeddings?.[0]?.values ?? [],
      model: m,
    };
  }

  async listModels(): Promise<string[]> {
    try {
      const response = await this.client.models.list();
      const models: string[] = [];
      for await (const m of response) {
        if (m.name) models.push(m.name.replace("models/", ""));
      }
      return models;
    } catch {
      return [this.activeModel];
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.client.models.get({ model: this.activeModel });
      return true;
    } catch (err) {
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
