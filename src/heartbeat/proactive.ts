/**
 * StackOwl — Proactive Pinger
 *
 * Makes Noctua feel alive — she proactively reaches out to the user
 * with reminders, morning briefs, ideas, and follow-ups.
 */

import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';
import { MemoryConsolidator } from './consolidation.js';
import { ToolPruner } from '../evolution/pruner.js';
import type { ToolRegistry } from '../tools/registry.js';
import type { CapabilityLedger } from '../evolution/ledger.js';
import type { LearningEngine } from '../learning/self-study.js';
import type { PreferenceStore } from '../preferences/store.js';
import type { ReflexionEngine } from '../evolution/reflexion.js';
import { SkillEvolver } from '../skills/evolver.js';
import { PatternMiner } from '../skills/pattern-miner.js';
import type { SkillsRegistry } from '../skills/registry.js';
import type { SessionStore } from '../memory/store.js';

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
    config: StackOwlConfig;
    capabilityLedger: CapabilityLedger;
    toolRegistry?: ToolRegistry;
    /** Callback to send a message to the user */
    sendToUser: (message: string) => Promise<void>;
    /** Get recent session history for context */
    getRecentHistory?: () => ChatMessage[];
    /** The user ID to run consolidation for */
    userId?: string;
    /** Learning engine for proactive self-study sessions */
    learningEngine?: LearningEngine;
    /** User preference store — used to check dynamic quiet hours */
    preferenceStore?: PreferenceStore;
    /** Reflexion engine for extracting rules from past failures */
    reflexionEngine?: ReflexionEngine;
    /** Skills registry for evolution pass */
    skillsRegistry?: SkillsRegistry;
    /** Absolute path to skills directory (for PatternMiner crystallization) */
    skillsDir?: string;
    /** Session store used by PatternMiner to read conversation history */
    sessionStore?: SessionStore;
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
    checkInIntervalMinutes: 20,  // base interval — actual timing is randomized ±50%
    morningBrief: true,
    morningBriefHour: 9,
    quietHoursStart: 22,
    quietHoursEnd: 7,
};

// Minimum time between ANY two pings (prevents spam even with short intervals)
const MIN_PING_COOLDOWN_MS = 5 * 60 * 1000; // 5 minutes

// ─── Proactive Pinger ────────────────────────────────────────────

export class ProactivePinger {
    private config: PingConfig;
    private context: PingContext;
    private engine: OwlEngine;
    private timers: NodeJS.Timeout[] = [];
    private lastPingTime: number = 0;
    private lastMorningBriefDate: string = '';
    private lastConsolidationDate: string = '';
    private lastSelfStudyDate: string = '';
    private lastDreamTime: number = 0;
    private lastSkillEvolutionDate: string = '';

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

        // Periodic check-in timer — use a shorter tick (1 min) and randomize
        // actual ping timing internally so it feels organic, not clockwork.
        const checkInTimer = setInterval(() => {
            this.maybeCheckIn().catch((err) => {
                console.error('[ProactivePinger] Check-in error:', err);
            });
        }, 60 * 1000); // tick every minute, maybeCheckIn decides whether to actually ping
        this.timers.push(checkInTimer);

        // Morning brief timer — check every minute around the brief hour
        const morningTimer = setInterval(() => {
            this.maybeMorningBrief().catch((err) => {
                console.error('[ProactivePinger] Morning brief error:', err);
            });
        }, 60 * 1000);
        this.timers.push(morningTimer);

        // 🧠 Daily Memory Consolidation timer
        const consolidationTimer = setInterval(() => {
            this.maybeConsolidateMemory().catch((err) => {
                console.error('[ProactivePinger] Memory consolidation error:', err);
            });
        }, 60 * 1000);
        this.timers.push(consolidationTimer);

        // 🧹 Autonomous Tool Pruning timer
        const pruningTimer = setInterval(() => {
            this.maybePruneTools().catch((err) => {
                console.error('[ProactivePinger] Tool pruning error:', err);
            });
        }, 60 * 1000);
        this.timers.push(pruningTimer);

        // 🧠 Proactive Self-Study timer
        const selfStudyTimer = setInterval(() => {
            this.maybeSelfStudy().catch((err) => {
                console.error('[ProactivePinger] Self-study error:', err);
            });
        }, 60 * 1000);
        this.timers.push(selfStudyTimer);

        // 🧠 Idle-Time Dreaming (Reflexion) timer
        const dreamTimer = setInterval(() => {
            this.maybeDream().catch((err) => {
                console.error('[ProactivePinger] Dream error:', err);
            });
        }, 60 * 1000);
        this.timers.push(dreamTimer);

        // 🌱 Skill Evolution + Pattern Mining timer (5 AM)
        const skillEvoTimer = setInterval(() => {
            this.maybeEvolveSkills().catch((err) => {
                console.error('[ProactivePinger] Skill evolution error:', err);
            });
        }, 60 * 1000);
        this.timers.push(skillEvoTimer);

        // Send a greeting on start — but respect quiet hours
        if (!this.isQuietHours()) {
            this.sendGreeting().catch((err) => {
                console.error('[ProactivePinger] Greeting error:', err);
            });
        }
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
     * User-configured quiet hours (from PreferenceStore) take priority over defaults.
     */
    private isQuietHours(): boolean {
        // Dynamic: if the user has set quiet hours via conversation, honour those
        if (this.context.preferenceStore) {
            return this.context.preferenceStore.isQuietHours(
                this.config.quietHoursStart,
                this.config.quietHoursEnd,
            );
        }
        // Static fallback from PingConfig
        const hour = new Date().getHours();
        if (this.config.quietHoursStart > this.config.quietHoursEnd) {
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

        const now = Date.now();

        // Enforce minimum cooldown between any two pings
        if (now - this.lastPingTime < MIN_PING_COOLDOWN_MS) return;

        // Randomize: only proceed if we've passed a random threshold within the interval window.
        // This makes pinging feel organic — sometimes 15 min, sometimes 35 min.
        const intervalMs = this.config.checkInIntervalMinutes * 60 * 1000;
        const timeSincePing = now - this.lastPingTime;
        // Probability of pinging increases linearly from 0 at MIN_COOLDOWN to 1.0 at 2x interval
        const probability = Math.min(1.0, (timeSincePing - MIN_PING_COOLDOWN_MS) / (intervalMs * 2));
        if (Math.random() > probability) return;

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
        if (this.isQuietHours()) return;

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
     * Maybe run the daily memory consolidation job.
     * Extracts persistent facts from the day's chat logs and saves them to owl_dna.json.
     */
    private async maybeConsolidateMemory(): Promise<void> {
        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const dateKey = now.toISOString().split('T')[0];

        // Run at 3 AM by default (when the user is asleep)
        // Hardcoded for now, but could be added to PingConfig
        if (hour !== 3 || minute !== 0) return;
        if (this.lastConsolidationDate === dateKey) return;

        this.lastConsolidationDate = dateKey;

        // Ensure we know who we are consolidating for
        const userId = this.context.userId;
        if (!userId) {
            console.log('[ProactivePinger] Skipping consolidation: no userId in context');
            return;
        }

        try {
            const consolidator = new MemoryConsolidator(this.context.provider, this.context.owl, this.context.config.workspace);
            await consolidator.consolidateSession(userId);
        } catch (e) {
            console.error('[ProactivePinger] Memory consolidation failed:', e);
        }
    }

    /**
     * Maybe run the autonomous tool pruner.
     * Scans for failing tools and attempts to rewrite or archive them.
     */
    private async maybePruneTools(): Promise<void> {
        const now = new Date();
        const hour = now.getHours();

        // Run every 4 hours (e.g. 0, 4, 8, 12, 16, 20)
        if (hour % 4 !== 0 || now.getMinutes() !== 0) return;

        const dateKey = `${now.toISOString().split('T')[0]}_${hour}`;
        if (this.lastConsolidationDate === dateKey) return; // Reusing this key variable slightly hackily for MVP, would normally track separately
        this.lastConsolidationDate = dateKey;

        try {
            // Provide the configured global ledger
            const pruner = new ToolPruner(
                this.context.provider,
                this.context.owl,
                this.context.config.workspace,
                this.context.capabilityLedger
            );
            await pruner.scanAndPrune();
        } catch (e) {
            console.error('[ProactivePinger] Tool pruning failed:', e);
        }
    }

    /**
     * Proactive self-study session — runs at 2 AM.
     * The owl quietly researches queued topics while the user is asleep.
     * No message is sent to the user; knowledge is stored as Pellets.
     */
    private async maybeSelfStudy(): Promise<void> {
        if (!this.context.learningEngine) return;

        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const dateKey = now.toISOString().split('T')[0];

        // Run at 2 AM, once per day
        if (hour !== 2 || minute !== 0) return;
        if (this.lastSelfStudyDate === dateKey) return;

        this.lastSelfStudyDate = dateKey;

        try {
            console.log('[ProactivePinger] 🧠 Starting overnight self-study session...');
            const result = await this.context.learningEngine.runStudySession(4);

            if (result.studied.length > 0) {
                console.log(
                    `[ProactivePinger] ✓ Self-study done: studied [${result.studied.join(', ')}], ` +
                    `${result.pelletsCreated} pellets created, ` +
                    `${result.newFrontierTopics.length} new topics discovered`
                );
            }
        } catch (err) {
            console.error('[ProactivePinger] Self-study session failed:', err);
        }
    }

    /**
     * Idle-Time Dreaming — reflects on past mistakes to adapt heuristics.
     */
    private async maybeDream(): Promise<void> {
        if (!this.context.reflexionEngine) return;

        const now = Date.now();
        // Run at most once every 15 minutes during idle time
        const DREAM_INTERVAL_MS = 15 * 60 * 1000;

        if (now - this.lastDreamTime < DREAM_INTERVAL_MS) return;
        this.lastDreamTime = now;

        try {
            await this.context.reflexionEngine.dream();
        } catch (err) {
            console.error('[ProactivePinger] Dream session failed:', err);
        }
    }

    /**
     * Skill Evolution + Pattern Mining — runs at 5 AM, once per day.
     *
     * Two phases:
     *   1. SkillEvolver: critiques every registered skill and rewrites low-scoring ones
     *      (Self-Refine loop, max 2 iterations each).
     *   2. PatternMiner: scans recent session history for repeated successful tool
     *      sequences and crystallizes them as new SKILL.md files (LATS-inspired).
     *
     * Neither phase sends anything to the user — all work is silent.
     */
    private async maybeEvolveSkills(): Promise<void> {
        if (!this.context.skillsRegistry) return;

        const now = new Date();
        const hour = now.getHours();
        const minute = now.getMinutes();
        const dateKey = now.toISOString().split('T')[0];

        // Run at 5 AM, once per day
        if (hour !== 5 || minute !== 0) return;
        if (this.lastSkillEvolutionDate === dateKey) return;

        this.lastSkillEvolutionDate = dateKey;

        // Phase 1: Evolve existing skills
        try {
            console.log('[ProactivePinger] 🌱 Starting overnight skill evolution pass...');
            const evolver = new SkillEvolver(this.context.provider, this.context.config);
            const report = await evolver.evolveAll(this.context.skillsRegistry);
            console.log(
                `[ProactivePinger] ✓ Skill evolution done: ` +
                `${report.improved}/${report.evaluated} improved, ` +
                `${report.failed} failed`
            );
        } catch (err) {
            console.error('[ProactivePinger] Skill evolution failed:', err);
        }

        // Phase 2: Mine new skills from conversation history
        if (this.context.sessionStore && this.context.skillsDir) {
            try {
                console.log('[ProactivePinger] ⛏️  Starting pattern mining pass...');
                const miner = new PatternMiner(
                    this.context.provider,
                    this.context.sessionStore,
                    this.context.config,
                );
                const newSkills = await miner.mine(
                    this.context.skillsRegistry,
                    this.context.skillsDir,
                );
                if (newSkills.length > 0) {
                    console.log(`[ProactivePinger] ✓ Pattern miner crystallized ${newSkills.length} new skill(s): [${newSkills.join(', ')}]`);
                } else {
                    console.log('[ProactivePinger] Pattern miner: no new patterns found');
                }
            } catch (err) {
                console.error('[ProactivePinger] Pattern mining failed:', err);
            }
        }
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
                config: this.context.config,
                toolRegistry: this.context.toolRegistry,
                cwd: this.context.config.workspace,
                skipGapDetection: true,  // Proactive messages are pre-generated — never evolve on them
            });

            await this.context.sendToUser(response.content);
            this.lastPingTime = Date.now();
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            console.error(`[ProactivePinger] Failed to generate ping: ${msg}`);
        }
    }
}
