/**
 * StackOwl — Owl Evolution Engine
 *
 * Analyzes recent conversation history to evolve an owl's DNA.
 * Owls learn user preferences and adjust their traits over time.
 */

import type { ChallengeLevel } from './persona.js';
import type { SessionStore } from '../memory/store.js';
import type { OwlRegistry } from './registry.js';
import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';

export class OwlEvolutionEngine {
    private engine: OwlEngine;
    private provider: ModelProvider;
    private config: StackOwlConfig;
    private sessionStore: SessionStore;
    private owlRegistry: OwlRegistry;

    constructor(provider: ModelProvider, config: StackOwlConfig, sessionStore: SessionStore, owlRegistry: OwlRegistry) {
        this.provider = provider;
        this.config = config;
        this.sessionStore = sessionStore;
        this.owlRegistry = owlRegistry;
        this.engine = new OwlEngine();
    }

    /**
     * Apply DNA decay toward neutral (0.5) if more than 7 days have passed since last decay.
     * Prevents stale preferences from dominating forever.
     */
    async applyDecayIfNeeded(owlName: string): Promise<boolean> {
        const owl = this.owlRegistry.get(owlName);
        if (!owl) return false;

        const decayRate = this.config.owlDna?.decayRatePerWeek ?? 0.01;
        if (decayRate <= 0) return false;

        const lastEvolved = owl.dna.lastEvolved
            ? new Date(owl.dna.lastEvolved).getTime()
            : 0;
        const daysSince = (Date.now() - lastEvolved) / (1000 * 60 * 60 * 24);

        if (daysSince < 7) return false;

        const weeksElapsed = Math.floor(daysSince / 7);
        const factor = decayRate * weeksElapsed;
        let changed = false;

        // Decay learnedPreferences toward neutral 0.5
        for (const key of Object.keys(owl.dna.learnedPreferences)) {
            const current = owl.dna.learnedPreferences[key];
            const decayed = current + (0.5 - current) * factor;
            owl.dna.learnedPreferences[key] = Math.max(0, Math.min(1, decayed));
            changed = true;
        }

        // Decay expertiseGrowth toward 0.5
        for (const key of Object.keys(owl.dna.expertiseGrowth)) {
            const current = owl.dna.expertiseGrowth[key];
            const decayed = current + (0.5 - current) * factor;
            owl.dna.expertiseGrowth[key] = Math.max(0, Math.min(1, decayed));
            changed = true;
        }

        if (changed) {
            owl.dna.lastEvolved = new Date().toISOString();
            await this.owlRegistry.saveDNA(owlName);
            console.log(`[Evolution] Applied ${weeksElapsed}-week DNA decay to ${owlName}.`);
        }

        return changed;
    }

    /**
     * Trigger an evolution pass for a specific owl.
     * Analyzes their most recent session and updates their DNA.
     */
    async evolve(owlName: string): Promise<boolean> {
        const owl = this.owlRegistry.get(owlName);
        if (!owl) throw new Error(`Owl ${owlName} not found.`);

        // 1. Get recent sessions for this owl
        // For simplicity, we just analyze the most recent session they participated in
        const allSessions = await this.sessionStore.listSessions();
        const owlSessions = allSessions.filter(s => s.metadata.owlName === owl.persona.name);

        if (owlSessions.length === 0) {
            console.log(`[Evolution] No sessions found for ${owl.persona.name}. Skipping evolution.`);
            return false;
        }

        const latestSession = owlSessions[0]; // Assuming listSessions sorts by newest

        if (latestSession.messages.length < 4) {
            console.log(`[Evolution] Session too short for ${owl.persona.name}. Skipping evolution.`);
            return false;
        }

        console.log(`\n🧬 [Evolution] ${owl.persona.emoji} ${owl.persona.name} is reflecting on their recent interactions...`);

        // 2. Ask the LLM to analyze the conversation
        const transcript = latestSession.messages.map(m => `[${m.role.toUpperCase()}]: ${m.content}`).join('\n\n');

        const prompt = `You are the subconscious of "${owl.persona.name}", analyzing a recent conversation to learn and evolve.\n\n` +
            `CURRENT DNA STATE:\n${JSON.stringify(owl.dna, null, 2)}\n\n` +
            `RECENT CONVERSATION:\n${transcript}\n\n` +
            `Task: Analyze the user's responses. Did they express annoyance at your verbosity? Did they explicitly state a preference (e.g., "I prefer Rust")? Did they reject your advice, or accept it?\n` +
            `Return a JSON object with proposed mutations to your DNA. Schema:\n` +
            `{\n` +
            `  "newPreferences": { "prefers_rust": 0.9, "hates_boilerplate": 0.8 }, // Add or update 0.0 to 1.0\n` +
            `  "traitAdjustments": {\n` +
            `    "verbosity": "concise", // or "balanced", "verbose"\n` +
            `    "challengeLevel": "low" // or "medium", "high", "relentless"\n` +
            `  },\n` +
            `  "expertiseGrowth": { "rust_macros": 0.1 }, // New sub-topics discussed (add +0.1 to current)\n` +
            `  "statsUpdate": {\n` +
            `    "adviceAccepted": true,\n` +
            `    "challengesGiven": 1\n` +
            `  },\n` +
            `  "evolutionReasoning": "User explicitly asked for shorter answers and chose Rust over Go."\n` +
            `}\n\n` +
            `Output ONLY valid JSON.`;

        const response = await this.engine.run(prompt, {
            provider: this.provider,
            owl,
            sessionHistory: [],
            config: this.config,
        });

        // 3. Parse JSON and apply mutations
        try {
            let jsonStr = response.content.trim();
            if (jsonStr.startsWith('```json')) jsonStr = jsonStr.replace(/^```json/, '').replace(/```$/, '').trim();
            else if (jsonStr.startsWith('```')) jsonStr = jsonStr.replace(/^```/, '').replace(/```$/, '').trim();

            const mutations = JSON.parse(jsonStr);

            // Apply modifications
            owl.dna.generation += 1;
            owl.dna.lastEvolved = new Date().toISOString();

            let changed = false;
            const logEntries: string[] = [];

            if (mutations.newPreferences) {
                for (const [k, v] of Object.entries(mutations.newPreferences)) {
                    owl.dna.learnedPreferences[k] = Number(v);
                    logEntries.push(`Learned preference: ${k} = ${v}`);
                    changed = true;
                }
            }

            if (mutations.traitAdjustments) {
                if (mutations.traitAdjustments.verbosity && mutations.traitAdjustments.verbosity !== owl.dna.evolvedTraits.verbosity) {
                    logEntries.push(`Verbosity changed: ${owl.dna.evolvedTraits.verbosity} -> ${mutations.traitAdjustments.verbosity}`);
                    owl.dna.evolvedTraits.verbosity = mutations.traitAdjustments.verbosity;
                    changed = true;
                }
                if (mutations.traitAdjustments.challengeLevel && mutations.traitAdjustments.challengeLevel !== owl.dna.evolvedTraits.challengeLevel) {
                    logEntries.push(`Challenge Level changed: ${owl.dna.evolvedTraits.challengeLevel} -> ${mutations.traitAdjustments.challengeLevel}`);
                    owl.dna.evolvedTraits.challengeLevel = mutations.traitAdjustments.challengeLevel as ChallengeLevel;
                    changed = true;
                }
            }

            if (mutations.expertiseGrowth) {
                for (const [k, amount] of Object.entries(mutations.expertiseGrowth)) {
                    const current = owl.dna.expertiseGrowth[k] || 0;
                    owl.dna.expertiseGrowth[k] = Math.min(1.0, current + Number(amount));
                    logEntries.push(`Grew expertise in ${k} (+${amount})`);
                    changed = true;
                }
            }

            if (mutations.statsUpdate) {
                owl.dna.interactionStats.totalConversations += 1;
                if (mutations.statsUpdate.adviceAccepted) {
                    // Moving average
                    owl.dna.interactionStats.adviceAcceptedRate = (owl.dna.interactionStats.adviceAcceptedRate * 0.9) + 0.1;
                }
                if (mutations.statsUpdate.challengesGiven) {
                    owl.dna.interactionStats.challengesGiven += Number(mutations.statsUpdate.challengesGiven);
                }
                changed = true;
            }

            if (changed) {
                owl.dna.evolutionLog.push({
                    generation: owl.dna.generation,
                    timestamp: owl.dna.lastEvolved,
                    mutations: logEntries,
                });

                // Keep log small
                if (owl.dna.evolutionLog.length > 20) {
                    owl.dna.evolutionLog.shift();
                }

                await this.owlRegistry.saveDNA(owl.persona.name);
                console.log(`✅ [Evolution] ${owl.persona.name} evolved to Generation ${owl.dna.generation}.`);
                console.log(`   ${chalk.dim(mutations.evolutionReasoning)}`);
                return true;
            } else {
                console.log(`[Evolution] ${owl.persona.name} analyzed session but found no reason to mutate.`);
                return false;
            }

        } catch (error) {
            console.error(`[Evolution] Failed to parse evolution JSON for ${owl.persona.name}:`, error);
            // Non-fatal, they just skip an evolution generation
            return false;
        }
    }
}

// Temporary chalk mock for the class since we don't pass it in, 
// normally we'd import chalk
import chalk from 'chalk';
