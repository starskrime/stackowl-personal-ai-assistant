/**
 * StackOwl — Ollama Provider (LangChain Backed)
 *
 * Connects to a local/remote Ollama instance via `@langchain/ollama`.
 */

import { ChatOllama, OllamaEmbeddings } from "@langchain/ollama";
import { LangChainProvider } from "./langchain.js";
import type { ProviderConfig } from "./base.js";

class OllamaProvider extends LangChainProvider {
    private baseUrl: string;

    constructor(baseUrl: string, chatModel: ChatOllama, defaultModel: string, embeddingModel: OllamaEmbeddings) {
        super('ollama', chatModel, defaultModel, embeddingModel);
        this.baseUrl = baseUrl;
    }

    async healthCheck(): Promise<boolean> {
        try {
            // A much faster health check for Ollama than loading a heavy model into memory
            const res = await fetch(`${this.baseUrl}/api/tags`, { method: 'GET', signal: AbortSignal.timeout(5000) });
            return res.ok;
        } catch {
            return false;
        }
    }
}

export function createOllamaProvider(config: ProviderConfig): LangChainProvider {
    const baseUrl = config.baseUrl ?? 'http://127.0.0.1:11434';
    const defaultModel = config.defaultModel ?? 'llama3.2';
    const defaultEmbeddingModel = config.defaultEmbeddingModel ?? 'nomic-embed-text';

    const chatModel = new ChatOllama({
        baseUrl,
        model: defaultModel,
    });

    const embeddingModel = new OllamaEmbeddings({
        baseUrl,
        model: defaultEmbeddingModel,
    });

    return new OllamaProvider(baseUrl, chatModel, defaultModel, embeddingModel);
}
