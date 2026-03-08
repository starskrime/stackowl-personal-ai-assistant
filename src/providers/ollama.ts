/**
 * StackOwl — Ollama Provider
 *
 * Connects to an Ollama instance (local or remote).
 * Supports chat, tool calling, streaming, and embeddings.
 */

import { Ollama } from 'ollama';
import type {
    ModelProvider,
    ChatMessage,
    ChatResponse,
    ChatOptions,
    ToolDefinition,
    StreamChunk,
    EmbeddingResponse,
    ProviderConfig,
    ToolCall,
} from './base.js';

export class OllamaProvider implements ModelProvider {
    readonly name = 'ollama';
    private client: Ollama;
    private defaultModel: string;
    private defaultEmbeddingModel: string;

    constructor(config: ProviderConfig) {
        const baseUrl = config.baseUrl ?? 'http://127.0.0.1:11434';
        this.client = new Ollama({ host: baseUrl });
        this.defaultModel = config.defaultModel ?? 'llama3.2';
        this.defaultEmbeddingModel = config.defaultEmbeddingModel ?? 'nomic-embed-text';
    }

    async chat(
        messages: ChatMessage[],
        model?: string,
        options?: ChatOptions
    ): Promise<ChatResponse> {
        const resolvedModel = model ?? this.defaultModel;

        try {
            const response = await this.client.chat({
                model: resolvedModel,
                messages: messages.map((m) => ({
                    role: m.role,
                    content: m.content,
                })),
                options: {
                    temperature: options?.temperature,
                    num_predict: options?.maxTokens,
                    top_p: options?.topP,
                    stop: options?.stop,
                },
                stream: false,
            });

            return {
                content: response.message.content,
                model: resolvedModel,
                finishReason: 'stop',
                usage: {
                    promptTokens: response.prompt_eval_count ?? 0,
                    completionTokens: response.eval_count ?? 0,
                    totalTokens: (response.prompt_eval_count ?? 0) + (response.eval_count ?? 0),
                },
            };
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`[OllamaProvider] Chat failed: ${message}`);
        }
    }

    async chatWithTools(
        messages: ChatMessage[],
        tools: ToolDefinition[],
        model?: string,
        options?: ChatOptions
    ): Promise<ChatResponse> {
        const resolvedModel = model ?? this.defaultModel;

        try {
            const ollamaTools = tools.map((tool) => ({
                type: 'function' as const,
                function: {
                    name: tool.name,
                    description: tool.description,
                    parameters: tool.parameters,
                },
            }));

            const response = await this.client.chat({
                model: resolvedModel,
                messages: messages.map((m) => ({
                    role: m.role,
                    content: m.content,
                })),
                tools: ollamaTools,
                options: {
                    temperature: options?.temperature,
                    num_predict: options?.maxTokens,
                    top_p: options?.topP,
                },
                stream: false,
            });

            const toolCalls: ToolCall[] = [];
            if (response.message.tool_calls) {
                for (const tc of response.message.tool_calls) {
                    toolCalls.push({
                        id: `tc_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
                        name: tc.function.name,
                        arguments: tc.function.arguments as Record<string, unknown>,
                    });
                }
            }

            return {
                content: response.message.content,
                toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
                model: resolvedModel,
                finishReason: toolCalls.length > 0 ? 'tool_calls' : 'stop',
                usage: {
                    promptTokens: response.prompt_eval_count ?? 0,
                    completionTokens: response.eval_count ?? 0,
                    totalTokens: (response.prompt_eval_count ?? 0) + (response.eval_count ?? 0),
                },
            };
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`[OllamaProvider] ChatWithTools failed: ${message}`);
        }
    }

    async *chatStream(
        messages: ChatMessage[],
        model?: string,
        options?: ChatOptions
    ): AsyncGenerator<StreamChunk> {
        const resolvedModel = model ?? this.defaultModel;

        try {
            const stream = await this.client.chat({
                model: resolvedModel,
                messages: messages.map((m) => ({
                    role: m.role,
                    content: m.content,
                })),
                options: {
                    temperature: options?.temperature,
                    num_predict: options?.maxTokens,
                    top_p: options?.topP,
                },
                stream: true,
            });

            for await (const chunk of stream) {
                yield {
                    content: chunk.message.content,
                    done: chunk.done,
                };
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`[OllamaProvider] Stream failed: ${message}`);
        }
    }

    async embed(text: string, model?: string): Promise<EmbeddingResponse> {
        const resolvedModel = model ?? this.defaultEmbeddingModel;

        try {
            const response = await this.client.embed({
                model: resolvedModel,
                input: text,
            });

            return {
                embedding: response.embeddings[0] as number[],
                model: resolvedModel,
            };
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`[OllamaProvider] Embed failed: ${message}`);
        }
    }

    async listModels(): Promise<string[]> {
        try {
            const response = await this.client.list();
            return response.models.map((m) => m.name);
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            throw new Error(`[OllamaProvider] ListModels failed: ${message}`);
        }
    }

    async healthCheck(): Promise<boolean> {
        try {
            await this.client.list();
            return true;
        } catch {
            return false;
        }
    }
}
