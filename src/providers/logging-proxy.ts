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

function previewMessages(messages: ChatMessage[]): string {
  return messages
    .map((m) => {
      const tag = m.toolCallId ? `[${m.role}:${m.toolCallId}]` : `[${m.role}]`;
      const body = (m.content ?? "").slice(0, 300);
      return `${tag} ${body}`;
    })
    .join("\n---\n");
}

/**
 * Transparent decorator that logs every provider call (prompts + responses) via log.engine.
 * Applied at the outermost layer in ProviderRegistry so all traffic is captured
 * regardless of protocol (OpenAI / Anthropic / Gemini / Grok).
 *
 * Prompt content: log.engine.debug (filterable)
 * Response summaries: log.engine.info
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
        log.engine.info("provider.chatWithToolsStream: entry", {
          provider: providerName,
          model: model ?? "(default)",
          messageCount: messages.length,
          toolCount: tools.length,
          toolNames: tools.map((t) => t.name),
          promptPreview: previewMessages(messages),
        });

        let eventCount = 0;
        try {
          for await (const event of innerStream(messages, tools, model, options)) {
            eventCount++;
            yield event;
          }
          log.engine.info("provider.chatWithToolsStream: exit", {
            provider: providerName,
            model: model ?? "(default)",
            eventCount,
            durationMs: Date.now() - start,
          });
        } catch (err) {
          log.engine.error("provider.chatWithToolsStream: failed", err as Error, {
            provider: providerName,
            model: model ?? "(default)",
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
    log.engine.info("provider.chat: entry", {
      provider: this.name,
      model: model ?? "(default)",
      messageCount: messages.length,
      roles: messages.map((m) => m.role),
      promptPreview: previewMessages(messages),
    });

    try {
      const response = await this.inner.chat(messages, model, options);
      log.engine.info("provider.chat: exit", {
        provider: this.name,
        model: response.model,
        finishReason: response.finishReason,
        contentLen: response.content?.length ?? 0,
        contentPreview: (response.content ?? "").slice(0, 400),
        usage: response.usage,
        durationMs: Date.now() - start,
      });
      return response;
    } catch (err) {
      log.engine.error("provider.chat: failed", err as Error, {
        provider: this.name,
        model: model ?? "(default)",
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
    log.engine.info("provider.chatWithTools: entry", {
      provider: this.name,
      model: model ?? "(default)",
      messageCount: messages.length,
      toolCount: tools.length,
      toolNames: tools.map((t) => t.name),
      roles: messages.map((m) => m.role),
      promptPreview: previewMessages(messages),
    });

    try {
      const response = await this.inner.chatWithTools(messages, tools, model, options);
      log.engine.info("provider.chatWithTools: exit", {
        provider: this.name,
        model: response.model,
        finishReason: response.finishReason,
        contentLen: response.content?.length ?? 0,
        contentPreview: (response.content ?? "").slice(0, 400),
        toolCallCount: response.toolCalls?.length ?? 0,
        toolCalls: response.toolCalls?.map((tc) => ({ name: tc.name, id: tc.id })),
        usage: response.usage,
        durationMs: Date.now() - start,
      });
      return response;
    } catch (err) {
      log.engine.error("provider.chatWithTools: failed", err as Error, {
        provider: this.name,
        model: model ?? "(default)",
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
    log.engine.info("provider.chatStream: entry", {
      provider: this.name,
      model: model ?? "(default)",
      messageCount: messages.length,
      roles: messages.map((m) => m.role),
      promptPreview: previewMessages(messages),
    });

    let chunkCount = 0;
    let totalContentLen = 0;
    try {
      for await (const chunk of this.inner.chatStream(messages, model, options)) {
        chunkCount++;
        totalContentLen += chunk.content?.length ?? 0;
        yield chunk;
      }
      log.engine.info("provider.chatStream: exit", {
        provider: this.name,
        model: model ?? "(default)",
        chunkCount,
        totalContentLen,
        durationMs: Date.now() - start,
      });
    } catch (err) {
      log.engine.error("provider.chatStream: failed", err as Error, {
        provider: this.name,
        model: model ?? "(default)",
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
      textPreview: text.slice(0, 100),
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
