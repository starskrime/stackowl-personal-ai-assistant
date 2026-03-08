/**
 * StackOwl — Instinct Engine
 *
 * Evaluates whether current conversation context triggers any of 
 * the owl's reactive instincts.
 */

import type { Instinct } from './registry.js';
import type { ModelProvider } from '../providers/base.js';
import type { OwlInstance } from '../owls/persona.js';
import { OwlEngine } from '../engine/runtime.js';

export class InstinctEngine {
    private engine: OwlEngine;

    constructor() {
        this.engine = new OwlEngine();
    }

    /**
     * Check if a user's message triggers any of the provided instincts.
     * Returns the triggered instinct, or null if none apply.
     */
    async evaluate(
        userMessage: string,
        availableInstincts: Instinct[],
        context: { provider: ModelProvider; owl: OwlInstance; model: string }
    ): Promise<Instinct | null> {
        if (availableInstincts.length === 0) return null;

        const { provider, owl, model } = context;

        // Build a prompt that asks the LLM to classify the input
        const instinctDescriptions = availableInstincts
            .map(i => `ID: ${i.name}\nConditions:\n- ${i.conditions.join('\n- ')}`)
            .join('\n\n');

        const prompt = `You are a classifier checking if a user's message triggers an "Instinct" (an automatic reflex constraint).\n\n` +
            `AVAILABLE INSTINCTS:\n${instinctDescriptions}\n\n` +
            `USER MESSAGE:\n"${userMessage}"\n\n` +
            `Task: Does the user's message match the conditions for any of the instincts? ` +
            `Return a JSON object: { "triggered": boolean, "instinctId": "name of instinct or null" }. ` +
            `Only trigger an instinct if the conditions are genuinely met. Output ONLY the JSON object.`;

        try {
            const response = await this.engine.run(prompt, {
                provider,
                owl,
                sessionHistory: [],
                model,
            });

            // Parse response
            let jsonStr = response.content.trim();
            if (jsonStr.startsWith('```json')) jsonStr = jsonStr.replace(/^```json/, '').replace(/```$/, '').trim();
            else if (jsonStr.startsWith('```')) jsonStr = jsonStr.replace(/^```/, '').replace(/```$/, '').trim();

            const parsed = JSON.parse(jsonStr);

            if (parsed.triggered && parsed.instinctId) {
                const triggeredInstinct = availableInstincts.find(i => i.name === parsed.instinctId);
                if (triggeredInstinct) {
                    console.log(`[Instinct Engine] ⚡ Triggered instinct: ${triggeredInstinct.name}`);
                    return triggeredInstinct;
                }
            }

            return null;
        } catch (error) {
            console.error('[Instinct Engine] Evaluation failed:', error);
            return null;
        }
    }
}
