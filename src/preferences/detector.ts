/**
 * StackOwl — Preference Detector
 *
 * Scans user messages for preference statements and extracts them
 * into structured entries for the PreferenceStore.
 *
 * Uses a two-tier approach:
 *   1. Regex pre-filter — cheap, runs on every message
 *   2. LLM extraction  — only fires when the pre-filter matches
 */

import type { ModelProvider } from '../providers/base.js';
import { PreferenceStore, PREF } from './store.js';
import { log } from '../logger.js';

// ─── Pre-filter keywords ─────────────────────────────────────────

const PREFERENCE_KEYWORDS = [
    'don\'t', 'do not', 'never', 'stop', 'always', 'prefer', 'want you to',
    'please don\'t', 'no messages', 'quiet', 'sleep', 'send separate', 'one by one',
    'remind me', 'language', 'speak', 'style', 'brief', 'short', 'detailed',
    'between', 'pm', 'am', 'at night', 'midnight', 'morning', 'evening',
    'disable', 'turn off', 'mute', 'focus on', 'avoid',
];

// ─── LLM extraction schema ────────────────────────────────────────

interface ExtractedPreference {
    key: string;
    value: unknown;
    channel: string;
}

// ─── Detector ────────────────────────────────────────────────────

export class PreferenceDetector {
    constructor(private provider: ModelProvider) {}

    /**
     * Analyse a user message for preference statements.
     * If any are found, they are stored in the PreferenceStore.
     * Returns the keys of preferences that were updated.
     */
    async detect(
        userMessage: string,
        store: PreferenceStore,
        channel = 'all',
    ): Promise<string[]> {
        // Tier 1: cheap pre-filter
        const lower = userMessage.toLowerCase();
        const hasKeyword = PREFERENCE_KEYWORDS.some(kw => lower.includes(kw));
        if (!hasKeyword) return [];

        // Tier 2: LLM extraction — but only run it if there's actually something new to learn.
        // If the message is very short (< 8 words) and all known preference keys already have
        // a value stored, skip the LLM call entirely. This prevents re-running extraction
        // every time the user says "keep it brief" when the preference is already set.
        const knownKeys = Object.values(PREF);
        const allAlreadySet = knownKeys.every(k => store.get(k, channel) !== undefined || store.get(k, 'all') !== undefined);
        const isShortRepeat = allAlreadySet && userMessage.trim().split(/\s+/).length < 12;
        if (isShortRepeat) {
            log.engine.info('[Preferences] All known preferences already set — skipping LLM extraction');
            return [];
        }

        try {
            const extracted = await this.extractWithLLM(userMessage, channel);
            if (extracted.length === 0) return [];

            const updated: string[] = [];
            for (const p of extracted) {
                // Skip keys that are already set to the exact same value — no-op
                const existing = store.get(p.key, p.channel) ?? store.get(p.key, 'all');
                if (JSON.stringify(existing) === JSON.stringify(p.value)) {
                    log.engine.info(`[Preferences] ${p.key} unchanged — skipping write`);
                    continue;
                }
                await store.set(p.key, p.value, userMessage, p.channel);
                updated.push(p.key);
                log.engine.info(`[Preferences] Stored: ${p.key} = ${JSON.stringify(p.value)} (channel: ${p.channel})`);
            }
            return updated;
        } catch (err) {
            log.engine.warn(`[Preferences] Detection failed: ${err instanceof Error ? err.message : String(err)}`);
            return [];
        }
    }

    private async extractWithLLM(
        message: string,
        channel: string,
    ): Promise<ExtractedPreference[]> {
        const systemPrompt = `You are a preference extractor for a personal AI assistant.
Extract any user preferences from the message below and return them as a JSON array.

Known preference keys and their value formats:
- "${PREF.QUIET_HOURS}": { "start": <hour 0-23>, "end": <hour 0-23> }  — e.g. "9 PM to 6 AM" → { "start": 21, "end": 6 }
- "${PREF.NEWS_FORMAT}": "separate" | "combined"  — how to deliver lists/news
- "${PREF.MESSAGE_STYLE}": "concise" | "detailed" | "normal"
- "${PREF.PROACTIVE_ENABLED}": true | false  — whether to send unsolicited messages
- "${PREF.LANGUAGE}": <language name or code>  — e.g. "Azerbaijani", "Spanish", "az"
- "${PREF.TOPICS_AVOID}": [<string>, ...]  — topics to not bring up proactively
- "${PREF.TOPICS_FOCUS}": [<string>, ...]  — topics to proactively surface

For each preference found, also set "channel": "${channel}" (or "all" if it applies to all channels).

Return ONLY a valid JSON array. If no preferences are found, return [].
Example: [{"key":"quiet_hours","value":{"start":21,"end":6},"channel":"all"}]`;

        const response = await this.provider.chat(
            [
                { role: 'system', content: systemPrompt },
                { role: 'user', content: `Message: "${message}"` },
            ],
            undefined,
            { temperature: 0, maxTokens: 256 },
        );

        const text = response.content.trim();

        // Extract JSON array from the response (handle cases where the LLM adds prose)
        const match = text.match(/\[[\s\S]*\]/);
        if (!match) return [];

        const parsed: unknown = JSON.parse(match[0]);
        if (!Array.isArray(parsed)) return [];

        // Validate shape
        return (parsed as ExtractedPreference[]).filter(
            p => p && typeof p.key === 'string' && p.value !== undefined,
        );
    }
}
