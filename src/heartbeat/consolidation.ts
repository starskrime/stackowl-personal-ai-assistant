/**
 * StackOwl — Daily Memory Consolidation
 *
 * Runs a background job (e.g., nightly) to compress raw session chat logs
 * into persistent facts and behavioral rules. It automatically updates
 * the owl's DNA so it learns from yesterday's successes and mistakes.
 */

import { join } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';

export class MemoryConsolidator {
    private provider: ModelProvider;
    private owl: OwlInstance;
    private engine: OwlEngine;
    private workspacePath: string;

    constructor(provider: ModelProvider, owl: OwlInstance, workspacePath: string) {
        this.provider = provider;
        this.owl = owl;
        this.workspacePath = workspacePath;
        this.engine = new OwlEngine();
    }

    /**
     * Reads a specific user's raw chat session, extracts new facts,
     * and updates the owl's learnedPreferences in its DNA.
     */
    async consolidateSession(userId: string): Promise<void> {
        console.log(`[Consolidator] 🧠 Starting memory consolidation for user: ${userId}`);

        const sessionPath = join(this.workspacePath, '.owl_sessions', `${userId}.json`);
        if (!existsSync(sessionPath)) {
            console.log(`[Consolidator] No session found for user: ${userId}. Skipping.`);
            return;
        }

        let sessionData;
        try {
            const raw = await readFile(sessionPath, 'utf-8');
            sessionData = JSON.parse(raw);
        } catch (e) {
            console.error(`[Consolidator] Failed to read session for ${userId}:`, e);
            return;
        }

        const messages: ChatMessage[] = sessionData.messages || [];
        if (messages.length < 5) {
            console.log(`[Consolidator] Session too short to consolidate.`);
            return;
        }

        // We only want to analyze recent messages to prevent hitting token limits
        // In a real system, we'd slice since the last consolidation timestamp
        const recentMessages = messages.slice(-100);

        const prompt =
            `You are a deep-learning memory extraction system.\n` +
            `Analyze the following chat log between the User and the Assistant (you).\n` +
            `Identify exactly 1-3 new, highly confident facts about the User's preferences, ` +
            `or explicit instructions the User gave you on how to behave.\n\n` +
            `Format your output ONLY as a valid JSON object matching this TypeScript interface:\n` +
            `{\n` +
            `  "extractedFacts": [\n` +
            `    { "fact": "User prefers concise answers", "weight": 1.0 },\n` +
            `    { "fact": "User hates using the word 'simply'", "weight": 0.2 }\n` +
            `  ]\n` +
            `}\n\n` +
            `If you find no new confident facts, return { "extractedFacts": [] }.\n\n` +
            `CHAT LOG:\n` +
            recentMessages.map(m => `[${m.role.toUpperCase()}] ${m.content}`).join('\n');

        let factsJson = '';
        try {
            // Give the engine an empty tool registry so it can't run tools during this background NLP task
            const response = await this.engine.run(prompt, {
                provider: this.provider,
                owl: this.owl,
                sessionHistory: [],
                config: {} as any, // Mock config for now
                skipGapDetection: true, // Do not trigger tool evolution from background consolidation
            });

            factsJson = response.content;

            // Basic JSON extraction to handle markdown blocks
            if (factsJson.includes('```json')) {
                factsJson = factsJson.split('```json')[1].split('```')[0].trim();
            } else if (factsJson.includes('```')) {
                factsJson = factsJson.split('```')[1].split('```')[0].trim();
            }

            const parsed = JSON.parse(factsJson) as { extractedFacts: { fact: string, weight: number }[] };

            if (parsed.extractedFacts && parsed.extractedFacts.length > 0) {
                console.log(`[Consolidator] 🧩 Found ${parsed.extractedFacts.length} new facts. updating DNA...`);
                await this.updateDNA(parsed.extractedFacts);
            } else {
                console.log(`[Consolidator] No new prominent facts discovered today.`);
            }

        } catch (e) {
            console.error(`[Consolidator] Failed to extract facts via LLM:`, e);
            console.error(`[Consolidator] Raw LLM Output:`, factsJson);
        }
    }

    private async updateDNA(newFacts: { fact: string, weight: number }[]): Promise<void> {
        // Read the current DNA from disk
        const dnaPath = join(this.owl.persona.sourcePath.replace('OWL.md', ''), 'owl_dna.json');
        if (!existsSync(dnaPath)) {
            console.error(`[Consolidator] DNA file not found at ${dnaPath}`);
            return;
        }

        try {
            const rawDna = await readFile(dnaPath, 'utf-8');
            const dnaData = JSON.parse(rawDna);

            if (!dnaData.learnedPreferences) {
                dnaData.learnedPreferences = {};
            }

            for (const f of newFacts) {
                // If the fact exists, average the weight. Otherwise, add it.
                if (dnaData.learnedPreferences[f.fact]) {
                    dnaData.learnedPreferences[f.fact] = (dnaData.learnedPreferences[f.fact] + f.weight) / 2;
                } else {
                    dnaData.learnedPreferences[f.fact] = f.weight;
                }
            }

            dnaData.lastEvolved = new Date().toISOString();

            await writeFile(dnaPath, JSON.stringify(dnaData, null, 2), 'utf-8');
            console.log(`[Consolidator] ✅ Successfully mutated DNA at ${dnaPath}`);

            // Update the runtime instance as well
            this.owl.dna = dnaData;

        } catch (e) {
            console.error(`[Consolidator] Failed to mutate DNA file:`, e);
        }
    }
}
