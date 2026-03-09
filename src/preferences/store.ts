/**
 * StackOwl — User Preference Store
 *
 * Persists user preferences expressed in natural conversation.
 * Examples: quiet hours, message formatting, communication style.
 *
 * Saved to workspace/preferences.json — survives restarts.
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

// ─── Types ───────────────────────────────────────────────────────

/**
 * Well-known preference keys. The owl may also store custom free-form preferences.
 */
export const PREF = {
    // Time: { start: number, end: number } in 24h (e.g. { start: 21, end: 6 })
    QUIET_HOURS: 'quiet_hours',
    // 'separate' | 'combined'
    NEWS_FORMAT: 'news_format',
    // 'concise' | 'detailed' | 'normal'
    MESSAGE_STYLE: 'message_style',
    // boolean — disable all proactive messages
    PROACTIVE_ENABLED: 'proactive_enabled',
    // string — preferred language code or name
    LANGUAGE: 'language',
    // string[] — topics to avoid in proactive messages
    TOPICS_AVOID: 'topics_avoid',
    // string[] — topics the user wants the owl to proactively surface
    TOPICS_FOCUS: 'topics_focus',
} as const;

export interface QuietHours {
    /** Start hour in 24h format, inclusive (e.g. 21 = 9 PM) */
    start: number;
    /** End hour in 24h format, exclusive (e.g. 6 = 6 AM) */
    end: number;
}

export interface UserPreference {
    key: string;
    value: unknown;
    /** The natural language statement that set this preference */
    source: string;
    /** Which channel this preference applies to ('all' if not channel-specific) */
    channel: string;
    updatedAt: string;
}

// ─── Store ───────────────────────────────────────────────────────

export class PreferenceStore {
    private prefs: Map<string, UserPreference> = new Map();
    private filePath: string;
    private loaded = false;

    constructor(workspacePath: string) {
        this.filePath = join(workspacePath, 'preferences.json');
    }

    async load(): Promise<void> {
        if (!existsSync(this.filePath)) { this.loaded = true; return; }
        try {
            const raw = await readFile(this.filePath, 'utf-8');
            const arr: UserPreference[] = JSON.parse(raw);
            for (const p of arr) this.prefs.set(this.key(p.key, p.channel), p);
        } catch { /* start fresh on corrupt file */ }
        this.loaded = true;
    }

    /**
     * Set or update a preference. Persists immediately.
     */
    async set(
        key: string,
        value: unknown,
        source: string,
        channel = 'all',
    ): Promise<void> {
        await this.ensureLoaded();
        this.prefs.set(this.key(key, channel), {
            key,
            value,
            source,
            channel,
            updatedAt: new Date().toISOString(),
        });
        await this.save();
    }

    /**
     * Get a preference value. Channel-specific preferences take priority over 'all'.
     */
    get<T = unknown>(key: string, channel = 'all'): T | undefined {
        const channelSpecific = this.prefs.get(this.key(key, channel));
        if (channelSpecific) return channelSpecific.value as T;
        if (channel !== 'all') {
            const generic = this.prefs.get(this.key(key, 'all'));
            if (generic) return generic.value as T;
        }
        return undefined;
    }

    getAll(): UserPreference[] {
        return [...this.prefs.values()];
    }

    /**
     * Returns true if the current time is within the user-configured quiet hours.
     * Falls back to provided defaults if the user hasn't configured them.
     */
    isQuietHours(defaultStart = 22, defaultEnd = 7): boolean {
        const prefs = this.get<QuietHours>(PREF.QUIET_HOURS);
        const start = prefs?.start ?? defaultStart;
        const end   = prefs?.end   ?? defaultEnd;
        const hour  = new Date().getHours();
        // Handles overnight ranges (e.g. 21-6)
        if (start > end) return hour >= start || hour < end;
        return hour >= start && hour < end;
    }

    /**
     * Returns a short block of text to inject into the system prompt so
     * the owl naturally honours preferences in every response.
     */
    toContextString(channel = 'all'): string {
        const all = this.getAll().filter(
            p => p.channel === 'all' || p.channel === channel,
        );
        if (all.length === 0) return '';

        const lines = all.map(p => {
            if (p.key === PREF.QUIET_HOURS) {
                const qh = p.value as QuietHours;
                return `- Do NOT send proactive messages between ${qh.start}:00 and ${qh.end}:00.`;
            }
            if (p.key === PREF.NEWS_FORMAT && p.value === 'separate') {
                return '- When sharing multiple news items or a list of results, send each item as a separate message using the onProgress callback, then confirm in the final response.';
            }
            if (p.key === PREF.MESSAGE_STYLE) {
                return `- Communication style: ${p.value}.`;
            }
            if (p.key === PREF.PROACTIVE_ENABLED && p.value === false) {
                return '- The user has disabled proactive messages. Do not check in or send unsolicited messages.';
            }
            if (p.key === PREF.LANGUAGE) {
                return `- Respond in: ${p.value}.`;
            }
            if (p.key === PREF.TOPICS_AVOID && Array.isArray(p.value)) {
                return `- Avoid proactively bringing up: ${(p.value as string[]).join(', ')}.`;
            }
            // Generic preference
            return `- ${p.source}`;
        });

        return '## User Preferences\n' + lines.join('\n') + '\n';
    }

    private key(prefKey: string, channel: string): string {
        return `${channel}::${prefKey}`;
    }

    private async ensureLoaded(): Promise<void> {
        if (!this.loaded) await this.load();
    }

    private async save(): Promise<void> {
        const dir = join(this.filePath, '..');
        if (!existsSync(dir)) await mkdir(dir, { recursive: true });
        await writeFile(
            this.filePath,
            JSON.stringify([...this.prefs.values()], null, 2),
            'utf-8',
        );
    }
}
