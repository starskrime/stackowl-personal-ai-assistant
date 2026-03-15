import { describe, it, expect } from 'vitest';
import { createDefaultDNA } from '../src/owls/persona.js';
import type { OwlDNA } from '../src/owls/persona.js';

describe('OwlDNA', () => {
    it('should create default DNA with correct initial values', () => {
        const dna = createDefaultDNA('TestOwl', 'medium');

        expect(dna.owl).toBe('TestOwl');
        expect(dna.generation).toBe(0);
        expect(dna.evolvedTraits.challengeLevel).toBe('medium');
        expect(dna.evolvedTraits.verbosity).toBe('balanced');
        expect(dna.evolvedTraits.humor).toBe(0.3);
        expect(dna.evolvedTraits.formality).toBe(0.5);
        expect(dna.interactionStats.totalConversations).toBe(0);
        expect(dna.interactionStats.adviceAcceptedRate).toBe(0.5);
        expect(dna.evolutionLog).toHaveLength(0);
    });

    it('should respect the provided challenge level', () => {
        const low = createDefaultDNA('LowOwl', 'low');
        const relentless = createDefaultDNA('HardOwl', 'relentless');

        expect(low.evolvedTraits.challengeLevel).toBe('low');
        expect(relentless.evolvedTraits.challengeLevel).toBe('relentless');
    });

    it('should have valid ISO timestamps', () => {
        const dna = createDefaultDNA('TimeOwl', 'high');

        expect(() => new Date(dna.created)).not.toThrow();
        expect(new Date(dna.created).getTime()).not.toBeNaN();
        expect(dna.created).toBe(dna.lastEvolved);
    });
});

describe('DNA Mutation Simulation', () => {
    let dna: OwlDNA;

    function simulateMutation(dna: OwlDNA, preferences: Record<string, number>) {
        dna.generation += 1;
        dna.lastEvolved = new Date().toISOString();
        for (const [k, v] of Object.entries(preferences)) {
            dna.learnedPreferences[k] = Math.max(0.05, Math.min(0.95, v));
        }
    }

    function simulateDecay(dna: OwlDNA, weeksElapsed: number, decayRate = 0.01) {
        const factor = decayRate * weeksElapsed;
        for (const key of Object.keys(dna.learnedPreferences)) {
            const current = dna.learnedPreferences[key];
            dna.learnedPreferences[key] = Math.max(0, Math.min(1, current + (0.5 - current) * factor));
        }
    }

    it('should clamp preferences to [0.05, 0.95]', () => {
        dna = createDefaultDNA('ClampOwl', 'medium');
        simulateMutation(dna, { extreme_high: 1.5, extreme_low: -0.5 });

        expect(dna.learnedPreferences.extreme_high).toBe(0.95);
        expect(dna.learnedPreferences.extreme_low).toBe(0.05);
    });

    it('should decay preferences toward 0.5 over time', () => {
        dna = createDefaultDNA('DecayOwl', 'medium');
        dna.learnedPreferences.likes_rust = 0.9;
        dna.learnedPreferences.hates_java = 0.1;

        simulateDecay(dna, 10, 0.05);

        // After decay, values should be closer to 0.5
        expect(dna.learnedPreferences.likes_rust).toBeLessThan(0.9);
        expect(dna.learnedPreferences.likes_rust).toBeGreaterThan(0.5);
        expect(dna.learnedPreferences.hates_java).toBeGreaterThan(0.1);
        expect(dna.learnedPreferences.hates_java).toBeLessThan(0.5);
    });

    it('should increment generation on each mutation', () => {
        dna = createDefaultDNA('GenOwl', 'medium');
        expect(dna.generation).toBe(0);

        simulateMutation(dna, { pref1: 0.7 });
        expect(dna.generation).toBe(1);

        simulateMutation(dna, { pref2: 0.3 });
        expect(dna.generation).toBe(2);
    });

    it('should track expertise growth with cap at 0.95', () => {
        dna = createDefaultDNA('ExpertOwl', 'medium');

        // Simulate expertise growth
        const topic = 'rust_macros';
        dna.expertiseGrowth[topic] = 0;
        dna.expertiseGrowth[topic] = Math.min(0.95, dna.expertiseGrowth[topic] + 0.3);
        expect(dna.expertiseGrowth[topic]).toBe(0.3);

        dna.expertiseGrowth[topic] = Math.min(0.95, dna.expertiseGrowth[topic] + 0.3);
        expect(dna.expertiseGrowth[topic]).toBe(0.6);

        dna.expertiseGrowth[topic] = Math.min(0.95, dna.expertiseGrowth[topic] + 0.5);
        expect(dna.expertiseGrowth[topic]).toBe(0.95);
    });

    it('should compute moving average for advice accepted rate', () => {
        dna = createDefaultDNA('AdviceOwl', 'medium');
        expect(dna.interactionStats.adviceAcceptedRate).toBe(0.5);

        // Accept advice (rate goes up)
        dna.interactionStats.adviceAcceptedRate = dna.interactionStats.adviceAcceptedRate * 0.9 + 0.1;
        expect(dna.interactionStats.adviceAcceptedRate).toBeGreaterThan(0.5);

        // Keep accepting (rate converges toward 1)
        for (let i = 0; i < 20; i++) {
            dna.interactionStats.adviceAcceptedRate = dna.interactionStats.adviceAcceptedRate * 0.9 + 0.1;
        }
        expect(dna.interactionStats.adviceAcceptedRate).toBeGreaterThan(0.9);
    });

    it('should keep evolution log bounded', () => {
        dna = createDefaultDNA('LogOwl', 'medium');
        const MAX_LOG = 20;

        for (let i = 0; i < 30; i++) {
            dna.evolutionLog.push({
                generation: i,
                timestamp: new Date().toISOString(),
                mutations: [`mutation_${i}`],
            });
            if (dna.evolutionLog.length > MAX_LOG) {
                dna.evolutionLog.shift();
            }
        }

        expect(dna.evolutionLog).toHaveLength(MAX_LOG);
        expect(dna.evolutionLog[0].generation).toBe(10); // First 10 got shifted out
    });
});
