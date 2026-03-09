/**
 * StackOwl — LangChain Provider Adapter
 *
 * Implements StackOwl's `ModelProvider` interface but delegates actual execution
 * to LangChain's `BaseChatModel` and `Embeddings` classes.
 * This allows unified integration with Ollama, Anthropic, OpenAI, etc.
 */

import type { BaseChatModel } from "@langchain/core/language_models/chat_models";
import type { Embeddings } from "@langchain/core/embeddings";
import { HumanMessage, SystemMessage, AIMessage, ToolMessage } from "@langchain/core/messages";
import { tool } from "@langchain/core/tools";
import { z } from "zod";
import type {
    ModelProvider,
    ChatMessage as StackOwlMessage,
    ChatResponse,
    ChatOptions,
    ToolDefinition,
    StreamChunk,
    EmbeddingResponse,
    ToolCall
} from "./base.js";

function convertToLangChainMessage(msg: StackOwlMessage) {
    if (msg.role === 'system') return new SystemMessage(msg.content);
    if (msg.role === 'user') return new HumanMessage(msg.content);
    if (msg.role === 'tool' && msg.toolCallId) {
        return new ToolMessage({
            content: msg.content,
            tool_call_id: msg.toolCallId,
            name: msg.name || 'tool',
        });
    }

    // Assistant role
    const aiMessageArgs: any = { content: msg.content };
    if (msg.toolCalls && msg.toolCalls.length > 0) {
        aiMessageArgs.tool_calls = msg.toolCalls.map(tc => ({
            id: tc.id,
            name: tc.name,
            args: tc.arguments
        }));
    }
    return new AIMessage(aiMessageArgs);
}

// Convert JSON Schema to Zod for LangChain tool binding (simplified for StackOwl's basic schemas)
function jsonSchemaToZod(schema: any): z.ZodTypeAny {
    const props: Record<string, z.ZodTypeAny> = {};
    if (schema.properties) {
        for (const [key, val] of Object.entries<any>(schema.properties)) {
            let zType: z.ZodTypeAny = z.any();
            if (val.type === 'string') {
                zType = z.string();
                if (val.enum) {
                    zType = z.enum(val.enum as [string, ...string[]]);
                }
            } else if (val.type === 'number') {
                zType = z.number();
            } else if (val.type === 'boolean') {
                zType = z.boolean();
            }

            if (val.description) {
                zType = zType.describe(val.description);
            }

            if (!schema.required?.includes(key)) {
                zType = zType.optional();
            }
            props[key] = zType;
        }
    }
    return z.object(props);
}

export class LangChainProvider implements ModelProvider {
    readonly name: string;
    private chatModel: BaseChatModel;
    private embeddingModel?: Embeddings;
    private defaultModel: string;

    constructor(
        name: string,
        chatModel: BaseChatModel,
        defaultModel: string,
        embeddingModel?: Embeddings
    ) {
        this.name = name;
        this.chatModel = chatModel;
        this.defaultModel = defaultModel;
        this.embeddingModel = embeddingModel;
    }

    async chat(
        messages: StackOwlMessage[],
        modelOverride?: string,
        options?: ChatOptions
    ): Promise<ChatResponse> {
        let model: any = this.chatModel;

        // LangChain models usually take params at instantiation, but we can bind kwargs
        const kwargs: any = {};
        if (options?.temperature !== undefined) kwargs.temperature = options.temperature;
        if (options?.maxTokens !== undefined) kwargs.max_tokens = options.maxTokens;
        if (options?.topP !== undefined) kwargs.top_p = options.topP;
        if (modelOverride) kwargs.model = modelOverride;

        if (Object.keys(kwargs).length > 0) {
            if (typeof model.bind === 'function') {
                model = model.bind(kwargs);
            }
        }

        try {
            const lcMessages = messages.map(convertToLangChainMessage);
            const response = await model.invoke(lcMessages);

            const usage = response.response_metadata?.tokenUsage || response.response_metadata?.usage || {};
            return {
                content: typeof response.content === 'string' ? response.content : JSON.stringify(response.content),
                model: modelOverride ?? this.defaultModel,
                finishReason: 'stop',
                usage: {
                    promptTokens: usage.promptTokens ?? usage.input_tokens ?? usage.prompt_eval_count ?? 0,
                    completionTokens: usage.completionTokens ?? usage.output_tokens ?? usage.eval_count ?? 0,
                    totalTokens: usage.totalTokens ?? 0,
                }
            };
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`[${this.name}] Chat failed: ${msg}`);
        }
    }

    async chatWithTools(
        messages: StackOwlMessage[],
        tools: ToolDefinition[],
        modelOverride?: string,
        options?: ChatOptions
    ): Promise<ChatResponse> {
        let model: any = this.chatModel;
        const kwargs: any = {};
        if (options?.temperature !== undefined) kwargs.temperature = options.temperature;
        if (options?.maxTokens !== undefined) kwargs.max_tokens = options.maxTokens;
        if (modelOverride) kwargs.model = modelOverride;

        // Convert our tools to LangChain structured tools
        const lcTools = tools.map(t => {
            return tool(
                async () => "Not implemented in provider",
                {
                    name: t.name,
                    description: t.description,
                    schema: jsonSchemaToZod(t.parameters)
                }
            );
        });

        // Bind tools to model
        if (lcTools.length > 0) {
            if (typeof model.bindTools === 'function') {
                model = model.bindTools(lcTools, kwargs);
            } else {
                throw new Error(`[${this.name}] chatWithTools called but model does not support .bindTools()`);
            }
        } else if (Object.keys(kwargs).length > 0) {
            if (typeof model.bind === 'function') {
                model = model.bind(kwargs);
            }
        }

        try {
            const lcMessages = messages.map(convertToLangChainMessage);
            const response = await model.invoke(lcMessages);

            const toolCalls: ToolCall[] = [];
            if (response.tool_calls && response.tool_calls.length > 0) {
                for (const tc of response.tool_calls) {
                    toolCalls.push({
                        id: tc.id || `tc_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
                        name: tc.name,
                        arguments: tc.args
                    });
                }
            }

            const usage = response.response_metadata?.tokenUsage || response.response_metadata?.usage || {};
            return {
                content: typeof response.content === 'string' ? response.content : "",
                toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
                model: modelOverride ?? this.defaultModel,
                finishReason: toolCalls.length > 0 ? 'tool_calls' : 'stop',
                usage: {
                    promptTokens: usage.promptTokens ?? usage.input_tokens ?? usage.prompt_eval_count ?? 0,
                    completionTokens: usage.completionTokens ?? usage.output_tokens ?? usage.eval_count ?? 0,
                    totalTokens: usage.totalTokens ?? 0,
                }
            };
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`[${this.name}] ChatWithTools failed: ${msg}`);
        }
    }

    async *chatStream(
        messages: StackOwlMessage[],
        modelOverride?: string,
        options?: ChatOptions
    ): AsyncGenerator<StreamChunk> {
        let model: any = this.chatModel;
        const kwargs: any = {};
        if (options?.temperature !== undefined) kwargs.temperature = options.temperature;
        if (options?.maxTokens !== undefined) kwargs.max_tokens = options.maxTokens;
        if (modelOverride) kwargs.model = modelOverride;

        if (Object.keys(kwargs).length > 0) {
            if (typeof model.bind === 'function') {
                model = model.bind(kwargs);
            }
        }

        try {
            const lcMessages = messages.map(convertToLangChainMessage);
            const stream = await model.stream(lcMessages);

            for await (const chunk of stream) {
                yield {
                    content: typeof chunk.content === 'string' ? chunk.content : "",
                    done: false // Stream chunk, done logic handled by caller consuming full stream
                };
            }
            yield { content: "", done: true };
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`[${this.name}] Stream failed: ${msg}`);
        }
    }

    async embed(text: string, modelOverride?: string): Promise<EmbeddingResponse> {
        if (!this.embeddingModel) {
            throw new Error(`[${this.name}] Embeddings not supported or configured for this provider.`);
        }

        try {
            const embeddings = await this.embeddingModel.embedDocuments([text]);
            return {
                embedding: embeddings[0],
                model: modelOverride ?? "default-embedding-model"
            };
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            throw new Error(`[${this.name}] Embed failed: ${msg}`);
        }
    }

    async listModels(): Promise<string[]> {
        // LangChain doesn't have a unified listModels API, so we just return the default model configured.
        return [this.defaultModel];
    }

    async healthCheck(): Promise<boolean> {
        try {
            // A simple health check is pinging the model with a tiny request
            await this.chat([{ role: 'user', content: 'Ping' }], undefined, { maxTokens: 5 });
            return true;
        } catch (e) {
            return false;
        }
    }
}
