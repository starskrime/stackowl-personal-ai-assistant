/**
 * StackOwl — Agent Watch: Adapter Base
 *
 * Common interface all agent adapters implement.
 * Decouples the relay engine from the specifics of each agent protocol.
 */

// ─── Shared Types ─────────────────────────────────────────────────

export type RiskLevel = "low" | "medium" | "high";
export type Decision = "allow" | "deny";

export interface AgentQuestion {
  /** Short unique ID for this question (used in Telegram replies) */
  id: string;
  /** Which session this came from */
  sessionId: string;
  /** Human-readable description of what the agent wants to do */
  toolName: string;
  /** The raw arguments / preview */
  toolInput: Record<string, unknown>;
  /** Already-classified risk level */
  risk: RiskLevel;
  /** When the question was received */
  receivedAt: number;
  /** Raw hook payload, passed through for response */
  raw: Record<string, unknown>;
}

export interface AgentAdapter {
  readonly name: string;
  /**
   * Start listening/watching.
   * The adapter calls onQuestion() whenever the agent needs a decision.
   */
  start(onQuestion: (q: AgentQuestion) => Promise<Decision>): Promise<void>;
  /** Stop watching. */
  stop(): Promise<void>;
}
