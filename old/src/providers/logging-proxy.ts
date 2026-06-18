import { appendFile, mkdir } from "node:fs/promises";
import { join, dirname } from "node:path";
import type {
  ModelProvider,
  ChatMessage,
  ChatOptions,
  ChatResponse,
  EmbeddingResponse,
  StreamChunk,
  StreamEvent,
  ToolDefinition,
} from "./base.js";
import { log } from "../logger.js";
import { currentTrace } from "../infra/observability/context.js";
import { currentLogFilePath } from "../infra/observability/sinks/jsonl-file.js";

// ─── Prompt Trace File ───────────────────────────────────────────
// Full untruncated prompt/response log, written to the same directory
// as the main stackowl-YYYY-MM-DD.log so both files are co-located.
// Path: <workspace>/logs/prompt-trace-YYYY-MM-DD.log (JSONL)

function traceFilePath(): string {
  const mainLog = currentLogFilePath();
  const logsDir = mainLog ? dirname(mainLog) : "logs";
  const date = new Date().toISOString().slice(0, 10);
  return join(logsDir, `prompt-trace-${date}.log`);
}

async function ensureTraceDir(filePath: string): Promise<void> {
  await mkdir(dirname(filePath), { recursive: true });
}

function writeTrace(record: Record<string, unknown>): void {
  const ctx = currentTrace();
  const filePath = traceFilePath();
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    traceId: ctx?.traceId,
    spanId: ctx?.spanId,
    sessionId: ctx?.sessionId,
    userId: ctx?.userId,
    channelId: ctx?.channelId,
    owl: ctx?.owl,
    ...record,
  }) + "\n";

  ensureTraceDir(filePath)
    .then(() => appendFile(filePath, line))
    .catch(() => { /* non-critical — never let trace failures break the call */ });
}

// ─── Short summary for the main log ─────────────────────────────

function previewMessages(messages: ChatMessage[]): string {
  return messages
    .map((m) => {
      const tag = m.toolCallId ? `[${m.role}:${m.toolCallId}]` : `[${m.role}]`;
      const body = (m.content ?? "").slice(0, 200);
      return `${tag} ${body}`;
    })
    .join(" | ");
}

// ─── Proxy ───────────────────────────────────────────────────────

/**
 * Transparent decorator that logs every provider call at two levels:
 *
 *   Main log  — summary lines (info level): method, provider, model,
 *               message count, 200-char preview, usage, durationMs.
 *
 *   Trace file (logs/prompt-trace-YYYY-MM-DD.log) — full untruncated
 *               JSONL: complete messages[], tools[], options on request;
 *               complete content/toolCalls/usage on response.
 *
 * Applied at the outermost ProviderRegistry layer so ALL traffic is
 * captured regardless of protocol (OpenAI / Anthropic / Gemini / Grok).
 */
export class LoggingProviderProxy implements ModelProvider {
  readonly name: string;

  // Optional — only wired when inner implements it so callers' truthiness checks still work.
  readonly chatWithToolsStream?: (
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ) => AsyncGenerator<StreamEvent>;

  constructor(private readonly inner: ModelProvider) {
    this.name = inner.name;

    if (inner.chatWithToolsStream) {
      const innerStream = inner.chatWithToolsStream.bind(inner);
      const providerName = inner.name;

      this.chatWithToolsStream = async function* (messages, tools, model, options) {
        const start = Date.now();
        const resolvedModel = model ?? "(default)";

        log.engine.info("provider.chatWithToolsStream: entry", {
          provider: providerName,
          model: resolvedModel,
          messageCount: messages.length,
          toolCount: tools.length,
          toolNames: tools.map((t) => t.name),
          preview: previewMessages(messages),
        });
        writeTrace({
          dir: "→",
          provider: providerName,
          model: resolvedModel,
          method: "chatWithToolsStream",
          messageCount: messages.length,
          messages,
          tools,
          options,
        });

        let eventCount = 0;
        const accumulatedContent: string[] = [];
        try {
          for await (const event of innerStream(messages, tools, model, options)) {
            eventCount++;
            if (event.type === "text_delta") accumulatedContent.push(event.content);
            yield event;
          }
          const durationMs = Date.now() - start;
          log.engine.info("provider.chatWithToolsStream: exit", {
            provider: providerName,
            model: resolvedModel,
            eventCount,
            durationMs,
          });
          writeTrace({
            dir: "←",
            provider: providerName,
            model: resolvedModel,
            method: "chatWithToolsStream",
            eventCount,
            content: accumulatedContent.join(""),
            durationMs,
          });
        } catch (err) {
          log.engine.error("provider.chatWithToolsStream: failed", err as Error, {
            provider: providerName,
            model: resolvedModel,
            eventCount,
            durationMs: Date.now() - start,
          });
          throw err;
        }
      };
    }
  }

  async chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const start = Date.now();
    const resolvedModel = model ?? "(default)";

    log.engine.info("provider.chat: entry", {
      provider: this.name,
      model: resolvedModel,
      messageCount: messages.length,
      roles: messages.map((m) => m.role),
      preview: previewMessages(messages),
    });
    writeTrace({
      dir: "→",
      provider: this.name,
      model: resolvedModel,
      method: "chat",
      messageCount: messages.length,
      messages,
      options,
    });

    try {
      const response = await this.inner.chat(messages, model, options);
      const durationMs = Date.now() - start;
      log.engine.info("provider.chat: exit", {
        provider: this.name,
        model: response.model,
        finishReason: response.finishReason,
        contentLen: response.content?.length ?? 0,
        usage: response.usage,
        durationMs,
      });
      writeTrace({
        dir: "←",
        provider: this.name,
        model: response.model,
        method: "chat",
        finishReason: response.finishReason,
        content: response.content,
        toolCalls: response.toolCalls,
        usage: response.usage,
        durationMs,
      });
      return response;
    } catch (err) {
      log.engine.error("provider.chat: failed", err as Error, {
        provider: this.name,
        model: resolvedModel,
        durationMs: Date.now() - start,
      });
      throw err;
    }
  }

  async chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse> {
    const start = Date.now();
    const resolvedModel = model ?? "(default)";

    log.engine.info("provider.chatWithTools: entry", {
      provider: this.name,
      model: resolvedModel,
      messageCount: messages.length,
      toolCount: tools.length,
      toolNames: tools.map((t) => t.name),
      roles: messages.map((m) => m.role),
      preview: previewMessages(messages),
    });
    writeTrace({
      dir: "→",
      provider: this.name,
      model: resolvedModel,
      method: "chatWithTools",
      messageCount: messages.length,
      messages,
      tools,
      options,
    });

    try {
      const response = await this.inner.chatWithTools(messages, tools, model, options);
      const durationMs = Date.now() - start;
      log.engine.info("provider.chatWithTools: exit", {
        provider: this.name,
        model: response.model,
        finishReason: response.finishReason,
        contentLen: response.content?.length ?? 0,
        toolCallCount: response.toolCalls?.length ?? 0,
        toolCalls: response.toolCalls?.map((tc) => ({ name: tc.name, id: tc.id })),
        usage: response.usage,
        durationMs,
      });
      writeTrace({
        dir: "←",
        provider: this.name,
        model: response.model,
        method: "chatWithTools",
        finishReason: response.finishReason,
        content: response.content,
        toolCalls: response.toolCalls,
        usage: response.usage,
        durationMs,
      });
      return response;
    } catch (err) {
      log.engine.error("provider.chatWithTools: failed", err as Error, {
        provider: this.name,
        model: resolvedModel,
        durationMs: Date.now() - start,
      });
      throw err;
    }
  }

  async *chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk> {
    const start = Date.now();
    const resolvedModel = model ?? "(default)";

    log.engine.info("provider.chatStream: entry", {
      provider: this.name,
      model: resolvedModel,
      messageCount: messages.length,
      roles: messages.map((m) => m.role),
      preview: previewMessages(messages),
    });
    writeTrace({
      dir: "→",
      provider: this.name,
      model: resolvedModel,
      method: "chatStream",
      messageCount: messages.length,
      messages,
      options,
    });

    let chunkCount = 0;
    const accumulatedContent: string[] = [];
    try {
      for await (const chunk of this.inner.chatStream(messages, model, options)) {
        chunkCount++;
        if (chunk.content) accumulatedContent.push(chunk.content);
        yield chunk;
      }
      const durationMs = Date.now() - start;
      log.engine.info("provider.chatStream: exit", {
        provider: this.name,
        model: resolvedModel,
        chunkCount,
        totalContentLen: accumulatedContent.reduce((s, c) => s + c.length, 0),
        durationMs,
      });
      writeTrace({
        dir: "←",
        provider: this.name,
        model: resolvedModel,
        method: "chatStream",
        chunkCount,
        content: accumulatedContent.join(""),
        durationMs,
      });
    } catch (err) {
      log.engine.error("provider.chatStream: failed", err as Error, {
        provider: this.name,
        model: resolvedModel,
        chunkCount,
        durationMs: Date.now() - start,
      });
      throw err;
    }
  }

  async embed(text: string, model?: string): Promise<EmbeddingResponse> {
    const start = Date.now();
    log.engine.debug("provider.embed: entry", {
      provider: this.name,
      model: model ?? "(default)",
      textLen: text.length,
    });

    try {
      const response = await this.inner.embed(text, model);
      log.engine.debug("provider.embed: exit", {
        provider: this.name,
        model: response.model,
        embeddingDim: response.embedding.length,
        durationMs: Date.now() - start,
      });
      return response;
    } catch (err) {
      log.engine.error("provider.embed: failed", err as Error, {
        provider: this.name,
        model: model ?? "(default)",
        durationMs: Date.now() - start,
      });
      throw err;
    }
  }

  listModels(): Promise<string[]> {
    return this.inner.listModels();
  }

  healthCheck(): Promise<boolean> {
    return this.inner.healthCheck();
  }
}
