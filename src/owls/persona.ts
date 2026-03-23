/**
 * StackOwl — Owl Persona Types
 *
 * Type definitions for owl personalities, DNA, and persona metadata.
 */

// ─── Challenge Levels ────────────────────────────────────────────

export type ChallengeLevel = 'low' | 'medium' | 'high' | 'relentless';

// ─── Owl Persona (loaded from OWL.md) ────────────────────────────

export interface OwlPersona {
    name: string;
    type: string;
    emoji: string;
    challengeLevel: ChallengeLevel;
    specialties: string[];
    traits: string[];
    /** The full system prompt from the body of OWL.md */
    systemPrompt: string;
    /** Path to the OWL.md file */
    sourcePath: string;
}

// ─── Owl DNA (evolves over time) ─────────────────────────────────

export interface OwlDNA {
    owl: string;
    generation: number;
    created: string;
    lastEvolved: string;

    learnedPreferences: Record<string, number>;
    evolvedTraits: {
        challengeLevel: ChallengeLevel;
        verbosity: 'verbose' | 'balanced' | 'concise';
        humor: number;       // 0-1
        formality: number;   // 0-1
        /** How often to volunteer information unprompted (0=never, 1=always) */
        proactivity: number;
        /** Whether to attempt uncertain tool calls or play safe */
        riskTolerance: 'cautious' | 'moderate' | 'aggressive';
        /** Show examples vs give direct answers */
        teachingStyle: 'examples' | 'direct' | 'adaptive';
        /** Do it myself vs ask the user for approval */
        delegationPreference: 'autonomous' | 'collaborative' | 'confirmatory';
    };
    expertiseGrowth: Record<string, number>;
    /** Per-domain confidence: how assertive to be about recommendations in each topic */
    domainConfidence: Record<string, number>;

    interactionStats: {
        totalConversations: number;
        adviceAcceptedRate: number;
        challengesGiven: number;
        challengesAccepted: number;
        parliamentSessions: number;
    };

    evolutionLog: EvolutionEntry[];
}

export interface EvolutionEntry {
    generation: number;
    timestamp: string;
    mutations: string[];
    /** 0–1 effectiveness score for A/B testing of mutations */
    effectiveness?: number;
}

// ─── Owl Instance (runtime, persona + DNA combined) ──────────────

export interface OwlInstance {
    persona: OwlPersona;
    dna: OwlDNA;
}

// ─── Default DNA factory ─────────────────────────────────────────

export function createDefaultDNA(owlName: string, challengeLevel: ChallengeLevel): OwlDNA {
    const now = new Date().toISOString();
    return {
        owl: owlName,
        generation: 0,
        created: now,
        lastEvolved: now,
        learnedPreferences: {},
        evolvedTraits: {
            challengeLevel,
            verbosity: 'balanced',
            humor: 0.3,
            formality: 0.5,
            proactivity: 0.5,
            riskTolerance: 'moderate',
            teachingStyle: 'adaptive',
            delegationPreference: 'collaborative',
        },
        expertiseGrowth: {},
        domainConfidence: {},
        interactionStats: {
            totalConversations: 0,
            adviceAcceptedRate: 0.5,
            challengesGiven: 0,
            challengesAccepted: 0,
            parliamentSessions: 0,
        },
        evolutionLog: [],
    };
}
