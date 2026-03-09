/**
 * StackOwl — Knowledge Researcher
 *
 * Self-study loop: given a domain, the owl interviews itself —
 * generates targeted questions, answers them deeply, stores each
 * answer as a searchable Pellet, then updates the knowledge graph.
 *
 * The result: the next conversation that touches this domain will
 * have rich, pre-researched knowledge injected via pellet search.
 */

import { v4 as uuidv4 } from 'uuid';
import type { ModelProvider } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { StackOwlConfig } from '../config/loader.js';
import type { PelletStore, Pellet } from '../pellets/store.js';
import { KnowledgeGraphManager } from './knowledge-graph.js';
import { log } from '../logger.js';

export interface ResearchResult {
    domain: string;
    pellets: Pellet[];
    relatedTopics: string[];
}

export class KnowledgeResearcher {
    constructor(
        private provider: ModelProvider,
        private owl: OwlInstance,
        _config: StackOwlConfig,
        private pelletStore: PelletStore,
        private graphManager: KnowledgeGraphManager,
    ) {}

    /**
     * Research a domain deeply:
     *  1. Generate 3 targeted questions about the domain
     *  2. Answer each question comprehensively
     *  3. Store each answer as a Pellet (searchable in future conversations)
     *  4. Update knowledge graph with new depth + frontier topics
     */
    async research(domain: string, context?: string): Promise<ResearchResult> {
        log.evolution.evolve(`Self-study: researching "${domain}"...`);

        const questions = await this.generateQuestions(domain, context);
        const pellets: Pellet[] = [];
        const allRelatedTopics: string[] = [];

        for (const question of questions.slice(0, 3)) {
            try {
                const { pellet, relatedTopics } = await this.answerAndStore(domain, question);
                pellets.push(pellet);
                allRelatedTopics.push(...relatedTopics);
                log.evolution.evolve(`  ✓ "${question.slice(0, 70)}"`);
            } catch (err) {
                log.evolution.warn(`  ✗ Question failed: ${err instanceof Error ? err.message : err}`);
            }
        }

        // Update knowledge graph with what we learned
        const uniqueRelated = [...new Set(allRelatedTopics)]
            .filter(t => t.toLowerCase() !== domain.toLowerCase())
            .slice(0, 6);

        this.graphManager.recordStudy(domain, pellets.length, uniqueRelated);

        return { domain, pellets, relatedTopics: uniqueRelated };
    }

    /**
     * Generate 3 targeted, practical research questions for a domain.
     */
    private async generateQuestions(domain: string, context?: string): Promise<string[]> {
        const prompt =
            `You help an AI assistant become more knowledgeable about "${domain}".\n` +
            (context ? `Recent conversation context: ${context.slice(0, 500)}\n\n` : '\n') +
            `Generate exactly 3 highly specific, practical questions about "${domain}" ` +
            `that a personal AI assistant should know to help users better.\n\n` +
            `Focus on: real-world usage, common pitfalls, best practices, patterns.\n` +
            `Make questions specific enough that a deep answer would be genuinely useful.\n\n` +
            `Return ONLY a JSON array: ["question 1", "question 2", "question 3"]`;

        try {
            const response = await this.provider.chat([
                { role: 'system', content: 'You are a research question generator. Output only valid JSON arrays.' },
                { role: 'user', content: prompt },
            ]);

            let jsonStr = response.content.trim();
            if (jsonStr.startsWith('```')) {
                jsonStr = jsonStr.replace(/^```json?/, '').replace(/```$/, '').trim();
            }

            const parsed = JSON.parse(jsonStr);
            if (Array.isArray(parsed) && parsed.length > 0) {
                return parsed.filter((q): q is string => typeof q === 'string');
            }
        } catch {
            // Fallback questions
        }

        return [
            `What are the most important best practices for ${domain}?`,
            `What are the most common mistakes and pitfalls when working with ${domain}?`,
            `What are the key patterns and techniques that experts use with ${domain}?`,
        ];
    }

    /**
     * Answer a question deeply, extract related topics, and store as a Pellet.
     */
    private async answerAndStore(
        domain: string,
        question: string,
    ): Promise<{ pellet: Pellet; relatedTopics: string[] }> {
        const prompt =
            `You are ${this.owl.persona.name}, a knowledgeable AI assistant building your own expertise.\n\n` +
            `Research and answer this question thoroughly:\n` +
            `"${question}"\n\n` +
            `Provide a comprehensive, practical answer in structured markdown:\n\n` +
            `## Key Insight\n` +
            `(1-2 sentences capturing the most important thing to know)\n\n` +
            `## Details\n` +
            `(Thorough explanation with concrete examples, code snippets if relevant)\n\n` +
            `## Common Pitfalls\n` +
            `(What to avoid, what trips people up)\n\n` +
            `## Related Topics\n` +
            `(End with this exact line: RELATED_JSON: ["topic1", "topic2", "topic3"])`;

        const response = await this.provider.chat([
            {
                role: 'system',
                content:
                    `You are building a personal knowledge base. Generate accurate, practical, ` +
                    `well-structured knowledge that will help an AI assistant give better answers. ` +
                    `Be specific and concrete, not vague and generic.`,
            },
            { role: 'user', content: prompt },
        ]);

        // Extract related topics from the structured marker
        const relatedTopics: string[] = [];
        const relatedMatch = response.content.match(/RELATED_JSON:\s*(\[[\s\S]*?\])/);
        if (relatedMatch) {
            try {
                const parsed = JSON.parse(relatedMatch[1]);
                if (Array.isArray(parsed)) {
                    relatedTopics.push(...parsed.filter((t): t is string => typeof t === 'string'));
                }
            } catch { /* ignore malformed JSON */ }
        }

        // Clean content (remove the RELATED_JSON line from the stored pellet)
        const cleanContent = response.content
            .replace(/RELATED_JSON:\s*\[[\s\S]*?\]\s*/g, '')
            .trim();

        const slug =
            `learn-${domain.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '').slice(0, 30)}` +
            `-${uuidv4().substring(0, 6)}`;

        const pellet: Pellet = {
            id: slug,
            title: question,
            generatedAt: new Date().toISOString(),
            source: `self-study:${domain}`,
            owls: [this.owl.persona.name],
            tags: [domain.toLowerCase(), 'self-study', 'knowledge'],
            version: 1,
            content: cleanContent,
        };

        await this.pelletStore.save(pellet);

        return { pellet, relatedTopics };
    }
}
