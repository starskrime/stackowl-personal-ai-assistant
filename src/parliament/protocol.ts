/**
 * StackOwl — Parliament Protocol
 *
 * Defines the types and phases for multi-owl brainstorming sessions.
 */

import type { OwlInstance } from "../owls/persona.js";
import type { PerspectiveRole } from "./perspectives.js";

export type ParliamentPhase =
  | "setup"
  | "round1_position"
  | "round2_challenge"
  | "round3_synthesis"
  | "complete";

/** Callbacks for streaming parliament debate rounds live to the user. */
export interface ParliamentCallbacks {
  /** Called when a round begins. */
  onRoundStart?: (round: number, phase: ParliamentPhase) => Promise<void>;
  /** Called as each owl delivers their position (round 1). */
  onPositionReady?: (position: OwlPosition) => Promise<void>;
  /** Called when the challenger delivers cross-examination (round 2). */
  onChallengeReady?: (challenge: OwlChallenge) => Promise<void>;
  /** Called when synthesis is complete (round 3). */
  onSynthesisReady?: (synthesis: string, verdict: string) => Promise<void>;
}

export interface ParliamentConfig {
  topic: string;
  participants: OwlInstance[];
  contextMessages: { role: string; content: string }[];
  /** Optional: stream debate progress to the caller. */
  callbacks?: ParliamentCallbacks;
  /** Optional: assign specific perspective roles to owls. */
  perspectiveRoles?: PerspectiveRole[];
}

export interface OwlPosition {
  owlName: string;
  owlEmoji: string;
  position: "FOR" | "AGAINST" | "CONDITIONAL" | "NEUTRAL" | "ANALYSIS";
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
