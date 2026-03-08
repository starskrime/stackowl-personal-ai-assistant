/**
 * StackOwl — Proactive Pinger
 *
 * Makes Noctua feel alive — she proactively reaches out to the user
 * with reminders, morning briefs, ideas, and follow-ups.
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';

// ─── Types ───────────────────────────────────────────────────────

export interface PingConfig {
    /** Enable/disable proactive pinging */
    enabled: boolean;
    /** Interval in minutes between periodic check-ins */
    checkInIntervalMinutes: number;
    /** Enable morning brief */
    morningBrief: boolean;
    /** Morning brief hour (24h format) */
    morningBriefHour: number;
    /** Quiet hours — no pings during these hours */
    quietHoursStart: number;
    quietHoursEnd: number;
}

export interface PingContext {
    provider: ModelProvider;
    owl: OwlInstance;
    model: string;
    /** Callback to send a message to the user */
    sendToUser: (message: string) => Promise<void>;
    /** Get recent session history for context */
    getRecentHistory?: () => ChatMessage[];
}

export type PingType =
    | 'morning_brief'
    | 'check_in'
    | 'reminder'
    | 'idea'
    | 'follow_up';

// ─── Default Config ──────────────────────────────────────────────

const DEFAULT_PING_CONFIG: PingConfig = {
    enabled: true,
    checkInIntervalMinutes: 120, // every 2 hours
    morningBrief: true,
    morningBriefHour: 9,
    quietHoursStart: 22,
    quietHoursEnd: 7,
};

// ─── Proactive Pinger ────────────────────────────────────────────

export class ProactivePinger {
    private config: PingConfig;
    private context: PingContext;
    private engine: OwlEngine;
    private timers: NodeJS.Timeout[] = [];
    private lastPingTime: number = 0;
    private lastMorningBriefDate: string = '';

    constructor(context: PingContext, config?: Partial<PingConfig>) {
        this.config = { ...DEFAULT_PING_CONFIG, ...config };
        this.context = context;
        this.engine = new OwlEngine();
    }

    /**
     * Start the proactive pinging system.
     */
    start(): void {
        if (!this.config.enabled) return;

        console.log('[ProactivePinger] 🔔 Proactive pinging started');

        // Periodic check-in timer
        const checkInMs = this.config.checkInIntervalMinutes * 60 * 1000;
        const checkInTimer = setInterval(() => {
            this.maybeCheckIn().catch((err) => {
                console.error('[ProactivePinger] Check-in error:', err);
            });
        }, checkInMs);
        this.timers.push(checkInTimer);

        // Morning brief timer — check every minute around the brief hour
        const morningTimer = setInterval(() => {
            this.maybeMorningBrief().catch((err) => {
                console.error('[ProactivePinger] Morning brief error:', err);
            });
        }, 60 * 1000);
        this.timers.push(morningTimer);

        // Send a greeting on start
        this.sendGreeting().catch((err) => {
            console.error('[ProactivePinger] Greeting error:', err);
        });
    }

    /**
     * Stop all proactive pinging.
     */
    stop(): void {
        for (const timer of this.timers) {
            clearInterval(timer);
        }
        this.timers = [];
        console.log('[ProactivePinger] 🔕 Proactive pinging stopped');
    }

    /**
     * Check if we're in quiet hours.
     */
    private isQuietHours(): boolean {
        const hour = new Date().getHours();
        if (this.config.quietHoursStart > this.config.quietHoursEnd) {
            // Wraps around midnight (e.g., 22-7)
            return hour >= this.config.quietHoursStart || hour < this.config.quietHoursEnd;
        }
        return hour >= this.config.quietHoursStart && hour < this.config.quietHoursEnd;
    }

    /**
     * Send an initial greeting when the bot starts.
     */
    private async sendGreeting(): Promise<void> {
        const hour = new Date().getHours();
        let timeOfDay: string;

        if (hour < 12) timeOfDay = 'morning';
        else if (hour < 17) timeOfDay = 'afternoon';
        else timeOfDay = 'evening';

        const prompt =
            `Generate a brief, warm greeting for the user. It's ${timeOfDay}. ` +
            `You are their personal executive assistant owl, always by their side. ` +
            `Keep it to 1-2 sentences. Be natural, not robotic. ` +
            `If you have context about what they were working on, briefly reference it.`;

        await this.generateAndSend(prompt, 'check_in');
    }

    /**
     * Maybe send a periodic check-in.
     */
    private async maybeCheckIn(): Promise<void> {
        if (this.isQuietHours()) return;

        // Minimum 30 min between any pings
        const now = Date.now();
        if (now - this.lastPingTime < 30 * 60 * 1000) return;

        const hour = new Date().getHours();
        const recentHistory = this.context.getRecentHistory?.() ?? [];

        let prompt: string;

        if (recentHistory.length > 0) {
            // Has recent context — generate a contextual follow-up
            const lastTopics = recentHistory
                .filter((m) => m.role === 'user')
                .slice(-3)
                .map((m) => m.content)
                .join('; ');

            prompt =
                `The user has been working on: "${lastTopics}". ` +
                `Generate a brief proactive check-in. Options: ` +
                `follow up on something they mentioned, suggest a break if it's been a while, ` +
                `offer a related idea, or ask if they need anything. ` +
                `Keep it to 1-2 sentences. Be natural.`;
        } else {
            // No recent context — generic check-in
            if (hour >= 12 && hour <= 13) {
                prompt = 'Remind the user about lunch in a casual, caring way. 1 sentence.';
            } else if (hour >= 17 && hour <= 18) {
                prompt = 'Ask the user if they want a summary of what they accomplished today. 1 sentence.';
            } else {
                prompt =
                    'Generate a brief check-in asking if the user needs anything. ' +
                    'Be warm but not annoying. 1 sentence.';
            }
        }

        await this.generateAndSend(prompt, 'check_in');
    }

    /**
     * Maybe send the morning brief.
     */
    private async maybeMorningBrief(): Promise<void> {
        if (!this.config.morningBrief) return;

        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const dateKey = now.toISOString().split('T')[0];

        // Only fire at the configured hour, minute 0, once per day
        if (hour !== this.config.morningBriefHour || minute !== 0) return;
        if (this.lastMorningBriefDate === dateKey) return;

        this.lastMorningBriefDate = dateKey;

        const dayOfWeek = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][now.getDay()];

        const prompt =
            `It's ${dayOfWeek} morning. Generate a warm morning brief for the user. Include: ` +
            `1) A greeting, ` +
            `2) If you have context from recent conversations, mention what's pending or what they might want to focus on today, ` +
            `3) A motivational note or interesting thought. ` +
            `Keep it concise — 3-5 sentences max. Make it feel like a real assistant briefing.`;

        await this.generateAndSend(prompt, 'morning_brief');
    }

    /**
     * Generate a proactive message using the LLM and send it.
     */
    private async generateAndSend(prompt: string, _type: PingType): Promise<void> {
        try {
            const response = await this.engine.run(prompt, {
                provider: this.context.provider,
                owl: this.context.owl,
                sessionHistory: this.context.getRecentHistory?.() ?? [],
                model: this.context.model,
            });

            await this.context.sendToUser(response.content);
            this.lastPingTime = Date.now();
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            console.error(`[ProactivePinger] Failed to generate ping: ${msg}`);
        }
    }
}
