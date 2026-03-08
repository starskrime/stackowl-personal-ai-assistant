/**
 * StackOwl — Parliament Protocol
 *
 * Defines the types and phases for multi-owl brainstorming sessions.
 */

import type { OwlInstance } from '../owls/persona.js';

export type ParliamentPhase = 'setup' | 'round1_position' | 'round2_challenge' | 'round3_synthesis' | 'complete';

export interface ParliamentConfig {
    topic: string;
    participants: OwlInstance[];
    contextMessages: { role: string; content: string }[];
}

export interface OwlPosition {
    owlName: string;
    owlEmoji: string;
    position: 'FOR' | 'AGAINST' | 'CONDITIONAL' | 'NEUTRAL' | 'ANALYSIS';
    argument: string;
}

export interface OwlChallenge {
    owlName: string;
    targetOwl: string;
    challengeContent: string;
}

export interface ParliamentSession {
    id: string;
    config: ParliamentConfig;
    phase: ParliamentPhase;
    positions: OwlPosition[];
    challenges: OwlChallenge[];
    synthesis?: string;
    verdict?: string;
    startedAt: number;
    completedAt?: number;
}
