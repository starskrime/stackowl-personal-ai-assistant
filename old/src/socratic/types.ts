/**
 * StackOwl — Socratic Mode Types
 *
 * Toggle-able mode where the owl responds only with probing questions.
 */

export type SocraticSubMode =
  | "pure"
  | "guided"
  | "reflective"
  | "devils_advocate";

export interface SocraticConfig {
  enabled: boolean;
  defaultMode: SocraticSubMode;
  /** Max exchanges before auto-summarizing insights */
  maxExchanges: number;
}

export interface SocraticSession {
  sessionId: string;
  mode: SocraticSubMode;
  exchangeCount: number;
  activatedAt: string;
  /** Key insights extracted from the Socratic dialogue */
  insights: string[];
}
