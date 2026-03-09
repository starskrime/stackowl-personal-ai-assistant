/**
 * StackOwl — Perch Manager
 *
 * Manages "Perch Points" — observation hooks where owls can passively
 * monitor the environment (files, git, logs) and react.
 */

import type { OwlRegistry } from '../owls/registry.js';
import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';

export interface PerchEvent {
    type: 'file_change' | 'git_commit' | 'system_alert';
    source: string;
    details: string;
}

export interface PerchPoint {
    name: string;
    start(emit: (event: PerchEvent) => void): Promise<void>;
    stop(): void;
}

export class PerchManager {
    private perches: PerchPoint[] = [];
    private engine: OwlEngine;
    private provider: ModelProvider;
    private config: StackOwlConfig;
    private owlRegistry: OwlRegistry;
    /** Callback to broadcast a proactive message to all active channels */
    private broadcast?: (message: string) => Promise<void>;

    constructor(
        provider: ModelProvider,
        config: StackOwlConfig,
        owlRegistry: OwlRegistry,
        broadcast?: (message: string) => Promise<void>
    ) {
        this.provider = provider;
        this.config = config;
        this.owlRegistry = owlRegistry;
        this.broadcast = broadcast;
        this.engine = new OwlEngine();
    }

    /**
     * Register a new observation perch.
     */
    addPerch(perch: PerchPoint) {
        this.perches.push(perch);
    }

    /**
     * Start all perches and listen for events.
     */
    async startAll() {
        for (const perch of this.perches) {
            await perch.start((event) => this.handleEvent(event));
            console.log(`[PerchManager] Started watching from: ${perch.name}`);
        }
    }

    /**
     * Stop all perches.
     */
    stopAll() {
        for (const perch of this.perches) {
            perch.stop();
        }
    }

    /**
     * Handle an event fired by a Perch.
     */
    private async handleEvent(event: PerchEvent) {
        const owl = this.owlRegistry.getDefault(); // Usually Noctua
        if (!owl) return;

        // Ensure we don't spam. In a real system we'd throttle this heavily.
        // For MVP, we'll just fire the prompt.
        const prompt = `[PERCH EVENT DETECTED: ${event.type}]\n` +
            `Source: ${event.source}\n` +
            `Details: ${event.details}\n\n` +
            `Task: Briefly analyze this event. Should the user be warned? Is it interesting? ` +
            `Keep your response to 1-2 sentences. Begin with "🔭 PERCH ALERT:".`;

        try {
            const response = await this.engine.run(prompt, {
                provider: this.provider,
                owl,
                sessionHistory: [],
                config: this.config
            });

            const msg = `\n${owl.persona.emoji} ${owl.persona.name}: ${response.content}\n`;

            // Log to CLI if we can
            // Use ANSI clear line carriage return if we are in readline, but standard log is fine for now
            console.log(msg);

            // Broadcast to all registered channels via gateway callback
            if (this.broadcast) {
                await this.broadcast(response.content);
            }

        } catch (error) {
            console.error('[PerchManager] Failed to analyze event:', error);
        }
    }
}
