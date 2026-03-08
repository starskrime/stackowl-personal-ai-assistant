import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PelletStore } from '../src/pellets/store.js';
import type { ModelProvider } from '../src/providers/base.js';
import { rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

// Mock Provider
class MockProvider implements ModelProvider {
    name = 'mock';
    async chat() { return { content: '', model: '', finishReason: 'stop' as const }; }
    async chatWithTools() { return { content: '', model: '', finishReason: 'stop' as const }; }
    async *chatStream() { yield { done: true }; }
    async embed(text: string) {
        // Return dummy embeddings for testing cosine similarity
        // If text contains "ai", return high vector on dim 1
        // If "apple", return high vector on dim 2
        let embedding = [0, 0, 0];
        if (text.toLowerCase().includes('ai')) embedding = [1, 0, 0];
        else if (text.toLowerCase().includes('apple')) embedding = [0, 1, 0];
        else embedding = [0, 0, 1]; // neutral

        return { embedding, model: 'mock-embed' };
    }
    async listModels() { return []; }
    async healthCheck() { return true; }
}

describe('PelletStore with Semantic Search', () => {
    const testSpace = join(__dirname, '.test_workspace');
    let store: PelletStore;
    let provider: MockProvider;

    beforeEach(async () => {
        await rm(testSpace, { recursive: true, force: true }).catch(() => { });
        await mkdir(testSpace, { recursive: true });
        provider = new MockProvider();
        store = new PelletStore(testSpace, provider);
        await store.init();
    });

    it('should save a pellet, generate its embedding, and find it via semantic search', async () => {
        // 1. Save pellet about AI
        await store.save({
            id: 'ai-history',
            title: 'History of AI',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: ['Noctua'],
            tags: ['ai', 'history'],
            content: 'Artificial Intelligence started decades ago.',
            version: 1
        });

        // 2. Save pellet about Apples
        await store.save({
            id: 'fruit-apple',
            title: 'About Apples',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: ['Noctua'],
            tags: ['fruit'],
            content: 'Apples are delicious and crunchy.',
            version: 1
        });

        // 3. Search for "AI" -> Should return the ai-history first based on mock embedding cosine similarity
        const results = await store.search('Tell me about ai');

        expect(results).toHaveLength(2);
        expect(results[0].id).toBe('ai-history'); // Highest score

        // Ensure vector index was actually saved and parsed correctly
        const dbState = await store.listAll();
        expect(dbState).toHaveLength(2);
    });

    it('should fall back to text search if no provider is present', async () => {
        const textStore = new PelletStore(testSpace); // no provider
        await textStore.init();

        await textStore.save({
            id: 'text-test',
            title: 'Text fallback test',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: [],
            tags: ['fallback'],
            content: 'A wild fox appeared.',
            version: 1
        });

        const results = await textStore.search('fox');
        expect(results).toHaveLength(1);
        expect(results[0].id).toBe('text-test');
    });
});
