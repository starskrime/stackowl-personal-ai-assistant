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
        // Guard: cap total pellets to prevent unbounded growth.
        // When at capacity, evict the oldest pellets to make room.
        const MAX_PELLETS = 2000;
        const EVICT_COUNT = 10; // Free up 5 slots so we don't evict on every research call
        const existing = await this.pelletStore.listAll();
        if (existing.length >= MAX_PELLETS) {
            log.evolution.info(
                `Self-study: pellet store at capacity (${existing.length}/${MAX_PELLETS}) — evicting ${EVICT_COUNT} oldest pellets to make room`,
            );
            // listAll() returns sorted by most recent first, so oldest are at the end
            const toEvict = existing.slice(-EVICT_COUNT);
            for (const pellet of toEvict) {
                await this.pelletStore.delete(pellet.id);
                log.evolution.debug(`  evicted: "${pellet.title}" (${pellet.generatedAt})`);
            }
        }

        log.evolution.evolve(`Self-study: researching "${domain}"...`);

        const questions = await this.generateQuestions(domain, context);
        const pellets: Pellet[] = [];
        const allRelatedTopics: string[] = [];

        for (const question of questions.slice(0, 2)) {
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
            `An AI assistant just failed to help a user with "${domain}".\n` +
            (context ? `Here's what happened: ${context.slice(0, 500)}\n\n` : '\n') +
            `Generate exactly 2 specific, actionable questions that, once answered, ` +
            `would let the assistant handle this situation next time.\n\n` +
            `Focus on: HOW to do it (tools, APIs, commands, techniques).\n` +
            `Not theory — practical "here's how you actually do it" knowledge.\n\n` +
            `Return ONLY a JSON array: ["question 1", "question 2"]`;

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
            `How can an AI assistant practically help users with ${domain}? What tools, APIs, or approaches should it use?`,
            `What are the most common user requests related to ${domain} and how should an AI assistant handle each one?`,
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
            `Question: "${question}"\n\n` +
            `Write a CONCISE knowledge card (max 150 words) that answers this question.\n\n` +
            `Format:\n` +
            `**Answer:** (1-2 sentence direct answer)\n` +
            `**How to do it:** (concrete steps, tools, APIs, or commands — be specific)\n` +
            `**Example:** (one brief example if applicable)\n\n` +
            `End with: RELATED_JSON: ["topic1", "topic2", "topic3"]\n\n` +
            `Rules:\n` +
            `- Be concise. No filler. No disclaimers.\n` +
            `- Focus on WHAT TO DO, not theory.\n` +
            `- If it involves an API/tool, name the specific one.\n` +
            `- Max 150 words before the RELATED_JSON line.`;

        const response = await this.provider.chat([
            {
                role: 'system',
                content:
                    `You generate concise knowledge cards for an AI assistant. ` +
                    `Be direct and practical. Max 150 words. No fluff.`,
            },
            { role: 'user', content: prompt },
        ], undefined, { temperature: 0.2 });

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
