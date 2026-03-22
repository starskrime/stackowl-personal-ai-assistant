/**
 * StackOwl — Constellations Types
 *
 * Cross-pellet pattern mining: thematic links, contradictions, knowledge gaps.
 */

export type ConstellationType = 'theme' | 'contradiction' | 'gap' | 'evolution';

export interface ConstellationLink {
  pelletId: string;
  pelletTitle: string;
  relevance: number;
  excerpt: string;
}

export interface Constellation {
  id: string;
  type: ConstellationType;
  title: string;
  description: string;
  links: ConstellationLink[];
  /** LLM-generated insight about this pattern */
  insight: string;
  discoveredAt: string;
  /** Whether the user has been notified */
  notified: boolean;
}
