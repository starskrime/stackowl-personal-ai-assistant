/**
 * StackOwl — Model Provider Base Types & Interface
 *
 * Vendor-agnostic abstraction for AI model providers.
 * All providers (Ollama, OpenAI, Anthropic) implement this interface.
 */

// ─── Message Types ───────────────────────────────────────────────

export type MessageRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: MessageRole;
  content: string;
  name?: string;
  toolCallId?: string;
  toolCalls?: ToolCall[];
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ExecutionPolicy {
  /** Milliseconds before AbortController fires. Default: 30000 */
  timeoutMs?: number;
  /** Max retry attempts on transient failure. Default: 1 */
  maxRetries?: number;
  /** Delay between retries in ms. Default: 1000 */
  retryDelayMs?: number;
  /** Ordered list of fallback tool names to try on persistent failure */
  fallbackChain?: string[];
}

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<
      string,
      {
        type: string;
        description: string;
        enum?: string[];
        items?: { type: string };
      }
    >;
    required?: string[];
  };
  /** When true, this tool must not run concurrently with others. Default: false. */
  sequential?: boolean;
  /** When true, this tool is hidden from LLM definitions but still callable internally */
  deprecated?: boolean;
  /** Operating systems where this tool is available. Omit = all platforms */
  platforms?: NodeJS.Platform[];
  /** Capability tags for Cost-Weighted Tool Graph routing (Phase 7b) */
  capabilities?: string[];
  /** Execution policy: timeout, retries, fallback chain */
  executionPolicy?: ExecutionPolicy;
}

// ─── Response Types ──────────────────────────────────────────────

export interface ChatResponse {
  content: string;
  toolCalls?: ToolCall[];
  usage?: TokenUsage;
  model: string;
  finishReason: "stop" | "tool_calls" | "length" | "error";
}

export interface StreamChunk {
  content?: string;
  toolCalls?: ToolCall[];
  done: boolean;
}

// ─── Streaming Events (for streaming tool calls) ────────────────

export type StreamEvent =
  | { type: "text_delta"; content: string }
  | { type: "tool_start"; toolCallId: string; toolName: string }
  | { type: "tool_args_delta"; toolCallId: string; argsDelta: string }
  | {
      type: "tool_end";
      toolCallId: string;
      toolName: string;
      arguments: Record<string, unknown>;
    }
  | { type: "done"; usage?: TokenUsage };

export interface TokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

export interface EmbeddingResponse {
  embedding: number[];
  model: string;
}

// ─── Provider Config ─────────────────────────────────────────────

export interface ProviderConfig {
  name: string;
  /** Model file to use for protocol lookup (defaults to name) */
  profile?: string;
  baseUrl?: string;
  apiKey?: string;
  /** Active model override — replaces defaultModel */
  activeModel?: string;
  /** @deprecated Use activeModel instead */
  defaultModel?: string;
  defaultEmbeddingModel?: string;
  options?: Record<string, unknown>;
}

// ─── Provider Interface ──────────────────────────────────────────

export interface ModelProvider {
  /** Provider identifier (e.g. 'ollama', 'openai', 'anthropic') */
  readonly name: string;

  /**
   * Send a chat completion request.
   * Returns the full response after completion.
   */
  chat(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse>;

  /**
   * Send a chat completion request with tool definitions.
   * The model may return tool calls in the response.
   */
  chatWithTools(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): Promise<ChatResponse>;

  /**
   * Stream a chat completion response.
   * Yields chunks as they arrive.
   */
  chatStream(
    messages: ChatMessage[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamChunk>;

  /**
   * Stream a chat completion with tools, yielding fine-grained events.
   * Optional — providers that support it enable real-time streaming to channels.
   * Falls back to synchronous chatWithTools() when not implemented.
   */
  chatWithToolsStream?(
    messages: ChatMessage[],
    tools: ToolDefinition[],
    model?: string,
    options?: ChatOptions,
  ): AsyncGenerator<StreamEvent>;

  /**
   * Generate an embedding vector for the given text.
   */
  embed(text: string, model?: string): Promise<EmbeddingResponse>;

  /**
   * List available models from this provider.
   */
  listModels(): Promise<string[]>;

  /**
   * Check if the provider is reachable and configured.
   */
  healthCheck(): Promise<boolean>;
}

// ─── Chat Options ────────────────────────────────────────────────

export interface ChatOptions {
  temperature?: number;
  maxTokens?: number;
  topP?: number;
  stop?: string[];
  /** Optional cancellation signal. Providers that support it can pass this to their underlying APIs. */
  signal?: AbortSignal;
  /** Provider-specific options */
  raw?: Record<string, unknown>;
}
