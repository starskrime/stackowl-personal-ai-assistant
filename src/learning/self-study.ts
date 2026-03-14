/**
 * StackOwl — Learning Engine
 *
 * The central learning orchestrator. Two modes:
 *
 *  1. REACTIVE  — called after every conversation. Extracts topics/gaps,
 *     immediately researches anything the owl was uncertain about,
 *     registers new domains for later deep study.
 *
 *  2. PROACTIVE — called during quiet hours (e.g., 2 AM). Picks the top
 *     topics from the study queue and researches them deeply so the owl
 *     is smarter for tomorrow's conversations.
 *
 * Knowledge compounds: each session expands the frontier of what the owl
 * knows it should learn next. Over time it becomes a genuine expert on
 * the topics YOU care about.
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { StackOwlConfig } from '../config/loader.js';
import type { PelletStore } from '../pellets/store.js';
import { ConversationExtractor } from './extractor.js';
import { KnowledgeResearcher } from './researcher.js';
import { KnowledgeGraphManager } from './knowledge-graph.js';
import { log } from '../logger.js';

export interface StudySessionResult {
    studied: string[];
    pelletsCreated: number;
    newFrontierTopics: string[];
}

export class LearningEngine {
    private extractor: ConversationExtractor;
    private graphManager: KnowledgeGraphManager;

    constructor(
        private provider: ModelProvider,
        private owl: OwlInstance,
        private config: StackOwlConfig,
        private pelletStore: PelletStore,
        workspacePath: string,
    ) {
        this.extractor = new ConversationExtractor(provider);
        this.graphManager = new KnowledgeGraphManager(workspacePath);
    }

    /**
     * REACTIVE LEARNING — call after each conversation ends.
     *
     * - Extracts domains/topics/gaps from the transcript
     * - Registers domains in the knowledge graph (marks for future study)
     * - Immediately researches knowledge gaps (assistant was uncertain → high priority)
     */
    async processConversation(messages: ChatMessage[]): Promise<void> {
        // Count user messages — even a single user question that stumped the assistant
        // is worth learning from. We pass ALL messages (including tool results) to the
        // extractor so it can see what tools were tried and what failed.
        const userMessages = messages.filter(m => m.role === 'user');
        if (userMessages.length < 1) return; // Nothing to learn from

        try {
            await this.graphManager.load();

            log.evolution.evolve('Extracting learning signals from conversation...');
            const insights = await this.extractor.extract(messages);

            const hasAnything =
                insights.domains.length > 0 ||
                insights.knowledgeGaps.length > 0 ||
                insights.topics.length > 0;

            if (!hasAnything) {
                log.evolution.evolve('No learning signals found in this conversation.');
                return;
            }

            log.evolution.evolve(
                `Insights: ${insights.domains.length} domains, ` +
                `${insights.knowledgeGaps.length} gaps, ` +
                `${insights.topics.length} topics`
            );

            // Register all domains in the knowledge graph
            for (const domain of insights.domains) {
                this.graphManager.touchDomain(domain, 'conversation');
            }
            // Topics become study candidates too
            for (const topic of insights.topics) {
                this.graphManager.touchDomain(topic, 'conversation');
            }

            // Immediately research knowledge gaps — these are highest priority
            // because the owl demonstrably didn't know something in this conversation
            if (insights.knowledgeGaps.length > 0) {
                const researcher = new KnowledgeResearcher(
                    this.provider, this.owl, this.config, this.pelletStore, this.graphManager,
                );

                // Context from recent turns to make research more targeted
                const recentContext = messages
                    .filter((m: ChatMessage) => m.role === 'user' || m.role === 'assistant')
                    .slice(-6)
                    .map((m: ChatMessage) => (m.content ?? '').slice(0, 200))
                    .join(' ');

                // Research up to 2 gaps immediately (don't block too long)
                for (const gap of insights.knowledgeGaps.slice(0, 2)) {
                    await researcher.research(gap, recentContext).catch(err => {
                        log.evolution.warn(
                            `Gap research failed for "${gap}": ` +
                            `${err instanceof Error ? err.message : err}`
                        );
                    });
                }
            }

            await this.graphManager.save();

            const stats = this.graphManager.getStats();
            log.evolution.evolve(
                `Knowledge graph: ${stats.totalDomains} domains | ` +
                `avg depth ${Math.round(stats.avgDepth * 100)}% | ` +
                `queue: ${stats.studyQueueLength}`
            );
        } catch (err) {
            // Non-fatal — learning failure should never break the main flow
            log.evolution.warn(
                `Reactive learning failed (non-fatal): ` +
                `${err instanceof Error ? err.message : err}`
            );
        }
    }

    /**
     * PROACTIVE SELF-STUDY — call during quiet hours.
     *
     * Picks top topics from the study queue and researches each one deeply.
     * This is how the owl gets smarter overnight, without any user interaction.
     */
    async runStudySession(maxTopics = 3): Promise<StudySessionResult> {
        await this.graphManager.load();

        const queue = this.graphManager.getStudyQueue(maxTopics);
        if (queue.length === 0) {
            log.evolution.evolve('Self-study: nothing in queue — owl is caught up.');
            return { studied: [], pelletsCreated: 0, newFrontierTopics: [] };
        }

        log.evolution.evolve(
            `Self-study session starting: ${queue.length} topic(s) — ${queue.join(', ')}`
        );

        const researcher = new KnowledgeResearcher(
            this.provider, this.owl, this.config, this.pelletStore, this.graphManager,
        );

        const studied: string[] = [];
        let pelletsCreated = 0;
        const allNewFrontier: string[] = [];

        for (const topic of queue) {
            try {
                const result = await researcher.research(topic);
                studied.push(topic);
                pelletsCreated += result.pellets.length;
                allNewFrontier.push(...result.relatedTopics);
            } catch (err) {
                log.evolution.warn(
                    `Study failed for "${topic}": ` +
                    `${err instanceof Error ? err.message : err}`
                );
            }
        }

        await this.graphManager.save();

        const newFrontierTopics = [...new Set(allNewFrontier)];

        log.evolution.evolve(
            `Self-study complete: ${studied.length} topic(s) studied, ` +
            `${pelletsCreated} pellets created, ` +
            `${newFrontierTopics.length} new frontier topics discovered`
        );

        return { studied, pelletsCreated, newFrontierTopics };
    }

    /**
     * Get a human-readable learning report — shown in /learning command.
     */
    async getLearningReport(): Promise<string> {
        await this.graphManager.load();
        return this.graphManager.getFullReport();
    }

    /**
     * One-line summary for status displays.
     */
    async getSummaryLine(): Promise<string> {
        await this.graphManager.load();
        const stats = this.graphManager.getStats();
        const summary = this.graphManager.getDomainSummary();
        return (
            `${stats.totalDomains} domains known | ` +
            `avg depth ${Math.round(stats.avgDepth * 100)}% | ` +
            `${stats.studyQueueLength} queued\n` +
            `Top: ${summary}`
        );
    }
}
