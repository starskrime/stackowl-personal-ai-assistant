/**
 * StackOwl — Conversation Extractor
 *
 * Analyzes a completed conversation and extracts structured learning signals:
 * - Topics discussed (specific)
 * - Domains involved (broad knowledge areas)
 * - Knowledge gaps (things the assistant was uncertain about)
 * - Research questions (worth studying deeper)
 */

import type { ChatMessage, ModelProvider } from '../providers/base.js';

export interface ConversationInsights {
    /** Specific topics that came up (e.g., "TypeScript generics", "Telegram bot rate limits") */
    topics: string[];
    /** Broad knowledge domains (e.g., "TypeScript", "Docker", "finance") */
    domains: string[];
    /** Things the assistant seemed uncertain or incomplete about */
    knowledgeGaps: string[];
    /** Concrete questions worth researching to improve future answers */
    researchQuestions: string[];
}

export class ConversationExtractor {
    constructor(private provider: ModelProvider) {}

    async extract(messages: ChatMessage[]): Promise<ConversationInsights> {
        const relevant = messages.filter(m => m.role === 'user' || m.role === 'assistant');
        if (relevant.length < 2) {
            return { topics: [], domains: [], knowledgeGaps: [], researchQuestions: [] };
        }

        const transcript = relevant
            .slice(-20)
            .map(m => `[${(m.role as string).toUpperCase()}]: ${(m.content ?? '').slice(0, 400)}`)
            .join('\n\n');

        const prompt =
            `Analyze this AI assistant conversation and extract learning opportunities.\n\n` +
            `CONVERSATION:\n${transcript}\n\n` +
            `Return ONLY a JSON object:\n` +
            `{\n` +
            `  "topics": ["specific topics discussed (max 5)"],\n` +
            `  "domains": ["broad knowledge domains like TypeScript/Docker/Finance (max 4)"],\n` +
            `  "knowledgeGaps": ["things the assistant was uncertain, vague, or wrong about (max 3)"],\n` +
            `  "researchQuestions": ["concrete questions worth researching to give better answers next time (max 3)"]\n` +
            `}\n\n` +
            `Be specific and actionable. Return [] for any list with nothing relevant.`;

        try {
            const response = await this.provider.chat([
                { role: 'system', content: 'You are a learning analyst. Output only valid JSON.' },
                { role: 'user', content: prompt },
            ]);

            let jsonStr = response.content.trim();
            if (jsonStr.startsWith('```')) {
                jsonStr = jsonStr.replace(/^```json?/, '').replace(/```$/, '').trim();
            }

            const parsed = JSON.parse(jsonStr);
            return {
                topics:            Array.isArray(parsed.topics)            ? parsed.topics            : [],
                domains:           Array.isArray(parsed.domains)           ? parsed.domains           : [],
                knowledgeGaps:     Array.isArray(parsed.knowledgeGaps)     ? parsed.knowledgeGaps     : [],
                researchQuestions: Array.isArray(parsed.researchQuestions) ? parsed.researchQuestions : [],
            };
        } catch {
            return { topics: [], domains: [], knowledgeGaps: [], researchQuestions: [] };
        }
    }
}
