/**
 * StackOwl — Model Provider Base Types & Interface
 *
 * Vendor-agnostic abstraction for AI model providers.
 * All providers (Ollama, OpenAI, Anthropic) implement this interface.
 */

// ─── Message Types ───────────────────────────────────────────────

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

export interface ChatMessage {
    role: MessageRole;
    content: string;
    name?: string;
    toolCallId?: string;
}

export interface ToolCall {
    id: string;
    name: string;
    arguments: Record<string, unknown>;
}

export interface ToolDefinition {
    name: string;
    description: string;
    parameters: {
        type: 'object';
        properties: Record<string, {
            type: string;
            description: string;
            enum?: string[];
        }>;
        required?: string[];
    };
}

// ─── Response Types ──────────────────────────────────────────────

export interface ChatResponse {
    content: string;
    toolCalls?: ToolCall[];
    usage?: TokenUsage;
    model: string;
    finishReason: 'stop' | 'tool_calls' | 'length' | 'error';
}

export interface StreamChunk {
    content?: string;
    toolCalls?: ToolCall[];
    done: boolean;
}

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
    baseUrl?: string;
    apiKey?: string;
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
        options?: ChatOptions
    ): Promise<ChatResponse>;

    /**
     * Send a chat completion request with tool definitions.
     * The model may return tool calls in the response.
     */
    chatWithTools(
        messages: ChatMessage[],
        tools: ToolDefinition[],
        model?: string,
        options?: ChatOptions
    ): Promise<ChatResponse>;

    /**
     * Stream a chat completion response.
     * Yields chunks as they arrive.
     */
    chatStream(
        messages: ChatMessage[],
        model?: string,
        options?: ChatOptions
    ): AsyncGenerator<StreamChunk>;

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
    /** Provider-specific options */
    raw?: Record<string, unknown>;
}
