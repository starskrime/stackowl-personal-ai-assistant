/**
 * Persona Engine — Core Types
 */

// ─── Traits ────────────────────────────────────────────────────────────────

/** A continuous trait that lives between 0 and 1 */
export type NumericTrait =
  | "warmth"
  | "formality"
  | "humor"
  | "verbosity"
  | "proactivity"
  | "empathy"
  | "directness"
  | "creativity";

/** A discrete trait with a fixed set of values */
export type ChallengeLevel = "low" | "medium" | "high" | "relentless";
export type TeachingStyle = "examples" | "direct" | "adaptive";
export type RiskTolerance = "cautious" | "moderate" | "aggressive";

export interface PersonaTraits {
  // Continuous 0–1
  warmth: number;
  formality: number;
  humor: number;
  verbosity: number;
  proactivity: number;
  empathy: number;
  directness: number;
  creativity: number;

  // Discrete
  challengeLevel: ChallengeLevel;
  teachingStyle: TeachingStyle;
  riskTolerance: RiskTolerance;

  // Domain expertise (topic → 0–1 confidence)
  domainExpertise: Record<string, number>;
}

// ─── Trait Bounds ──────────────────────────────────────────────────────────

/** Per-trait min/max clamps to prevent runaway drift */
export interface TraitBounds {
  warmth?: [number, number];
  formality?: [number, number];
  humor?: [number, number];
  verbosity?: [number, number];
  proactivity?: [number, number];
  empathy?: [number, number];
  directness?: [number, number];
  creativity?: [number, number];
  challengeLevel?: ChallengeLevel[];  // allowed values
  teachingStyle?: TeachingStyle[];
  riskTolerance?: RiskTolerance[];
}

// ─── Persona Definition ────────────────────────────────────────────────────

/** The base template — the "DNA blueprint" for a persona */
export interface PersonaDefinition {
  id: string;
  name: string;
  description: string;
  systemPromptTemplate: string;  // may reference {{trait_name}} placeholders
  baseTraits: PersonaTraits;
  bounds: TraitBounds;
  decayRatePerWeek: number;      // 0 = no decay, 0.05 = 5% per week back toward base
  version: string;
  createdAt: string;
}

// ─── Per-user Persona State ────────────────────────────────────────────────

export interface PersonaSnapshot {
  snapshotId: string;
  takenAt: string;
  reason: string;
  traits: PersonaTraits;
  generation: number;
}

export interface EvolutionEntry {
  generation: number;
  timestamp: string;
  mutations: string[];  // human-readable descriptions of what changed
  trigger: string;      // what caused this evolution
}

export interface PersonaState {
  personaId: string;
  userId: string;
  traits: PersonaTraits;       // evolved traits (bounded)
  baseTraits: PersonaTraits;   // copy of definition base traits (for diff)
  generation: number;
  lastEvolved: string;
  createdAt: string;
  evolutionLog: EvolutionEntry[];
  snapshots: PersonaSnapshot[];
  interactionCount: number;
}

// ─── Rendered Persona ─────────────────────────────────────────────────────

export interface RenderedPersona {
  personaId: string;
  userId: string;
  name: string;
  systemPrompt: string;
  traits: PersonaTraits;
  generation: number;
  lastEvolved: string;
}

// ─── Analytics ────────────────────────────────────────────────────────────

export interface TraitDelta {
  trait: string;
  base: number | string;
  current: number | string;
  delta: number | string;    // numeric diff or "changed"
}

export interface PersonaAnalytics {
  personaId: string;
  userId: string;
  generation: number;
  interactionCount: number;
  traitDeltas: TraitDelta[];
  topDomains: Array<{ domain: string; expertise: number }>;
  evolutionSummary: string;
}
