import { describe, it, expect, beforeEach } from 'vitest';
import { PelletStore } from '../src/pellets/store.js';
import { rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

describe('PelletStore with BM25 Search', () => {
    const testSpace = join(__dirname, '.test_workspace');
    let store: PelletStore;

    beforeEach(async () => {
        await rm(testSpace, { recursive: true, force: true }).catch(() => { });
        await mkdir(testSpace, { recursive: true });
        store = new PelletStore(testSpace);
        await store.init();
    });

    it('should save pellets and rank by BM25 relevance', async () => {
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

        // Search for "AI" -> ai-history should rank first (term in title + tags + content)
        const results = await store.search('Tell me about ai');
        expect(results.length).toBeGreaterThanOrEqual(1);
        expect(results[0].id).toBe('ai-history');

        const dbState = await store.listAll();
        expect(dbState).toHaveLength(2);
    });

    it('should work without a provider (no embeddings needed)', async () => {
        const textStore = new PelletStore(testSpace);
        await textStore.init();

        await textStore.save({
            id: 'text-test',
            title: 'Text search test',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: [],
            tags: ['fallback'],
            content: 'A wild fox appeared in the forest.',
            version: 1
        });

        const results = await textStore.search('fox');
        expect(results).toHaveLength(1);
        expect(results[0].id).toBe('text-test');
    });

    it('should handle delete and re-search correctly', async () => {
        await store.save({
            id: 'pellet-a',
            title: 'Kubernetes deployment guide',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: [],
            tags: ['kubernetes', 'devops'],
            content: 'Deploy pods using kubectl apply.',
            version: 1
        });

        await store.save({
            id: 'pellet-b',
            title: 'Docker container basics',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: [],
            tags: ['docker', 'devops'],
            content: 'Build images with Dockerfile.',
            version: 1
        });

        // Both should appear for "devops"
        let results = await store.search('devops');
        expect(results).toHaveLength(2);

        // Delete one
        await store.delete('pellet-a');

        // Only pellet-b should remain
        results = await store.search('devops');
        expect(results).toHaveLength(1);
        expect(results[0].id).toBe('pellet-b');
    });

    it('should rebuild index from existing files on cold start', async () => {
        // Save a pellet (this creates the md file + index)
        await store.save({
            id: 'cold-start',
            title: 'Cold start migration test',
            generatedAt: new Date().toISOString(),
            source: 'test',
            owls: [],
            tags: ['migration'],
            content: 'This tests the cold-start rebuild path.',
            version: 1
        });

        // Create a fresh store pointing at the same directory
        // (simulates restart with missing index)
        await rm(join(testSpace, 'pellets', 'tfidf_index.json'), { force: true });
        const freshStore = new PelletStore(testSpace);
        await freshStore.init(); // should rebuild index from .md files

        const results = await freshStore.search('migration');
        expect(results).toHaveLength(1);
        expect(results[0].id).toBe('cold-start');
    });
});
