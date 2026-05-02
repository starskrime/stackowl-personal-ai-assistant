import type { ChatMessage, ToolCall, ToolDefinition, StreamEvent } from "../providers/base.js";

// ─── Token Budget ─────────────────────────────────────────────────

export interface TokenBudget {
  total: number;
  used: number;
  remaining: number;
}

// ─── Turn Contract ────────────────────────────────────────────────

export interface TurnRequest {
  messages: ChatMessage[];
  tools: ToolDefinition[];
  modelName: string;
  providerName: string;
  sessionId: string;
  turnBudget: TokenBudget;
  onStreamEvent?: (event: StreamEvent) => Promise<void>;
  onProgress?: (msg: string) => Promise<void>;
  toolRegistry?: { execute(name: string, args: unknown, ctx: unknown): Promise<string> };
  _resolvedProvider?: import("../providers/base.js").ModelProvider;
  /** Short user message text — convenience alias used by GAV and context propagation */
  message?: string;
  /** Active sub-goal from TaskLedger — passed to GoalVerifier if present */
  activeSubGoal?: SubGoal;
  /** Original user message text — passed to GoalVerifier for context */
  userMessage?: string;
}

export interface FailedToolCall {
  name: string;
  reason: string;
}

export interface TurnResult {
  content: string;
  toolCalls: ToolCall[];
  toolResults: { toolCallId: string; name: string; result: string }[];
  tokensUsed: number;
  doneSignal: boolean;
  budgetExhausted: boolean;
  pendingCapabilityGap?: string;
  failedTools: FailedToolCall[];
  providerUsed: string;
  modelUsed: string;
}

// ─── Task Ledger ──────────────────────────────────────────────────

export type TaskComplexity = "simple" | "medium" | "complex" | "unbounded";
export type SubGoalStatus = "pending" | "in_progress" | "done" | "blocked" | "skipped";

export interface SubGoal {
  id: string;
  description: string;
  status: SubGoalStatus;
  dependsOn: string[];
  result?: string;
}

export interface TaskLedgerRevision {
  at: number;
  reason: string;
  previousGoal: string;
}

export interface TaskLedger {
  id: string;
  goal: string;
  subGoals: SubGoal[];
  expectedOutput: string;
  complexity: TaskComplexity;
  estimatedTurns: number;
  behavioralConstraints: string[];
  parliamentContext?: string;
  approachPatterns: string[];
  reflexionContext?: string;
  revisions: TaskLedgerRevision[];
  createdAt: number;
}

// ─── Health ───────────────────────────────────────────────────────

export type HealthSignalKind =
  | "spinning"
  | "tool_blackout"
  | "budget_critical"
  | "provider_unstable"
  | "stall";

export interface HealthSignal {
  kind: HealthSignalKind;
  detail: string;
  iteration: number;
}

export interface RunHealth {
  iteration: number;
  tokensConsumed: number;
  tokenBudget: number;
  consecutiveFailures: number;
  uniqueToolsAttempted: Set<string>;
  allToolsFailed: boolean;
  spinningDetected: boolean;
  providerSwitchCount: number;
  stuckOnSubGoalId: string | null;
  signals: HealthSignal[];
}

// ─── Recovery ─────────────────────────────────────────────────────

export type Decision =
  | "CONTINUE"
  | "REPLAN"
  | "HITL"
  | "SYNTHESIZE"
  | "DEGRADE";

// ─── HITL ─────────────────────────────────────────────────────────

export interface HitlMemo {
  whatIDid: string;
  whatINeed: string;
  options?: string[];
  recommendation?: string;
}

export interface HitlRequest {
  kind: "approval" | "clarification" | "choice";
  memo: HitlMemo;
  ledgerSnapshot: TaskLedger;
  pendingAction: string;
}

export interface HitlResponse {
  approved: boolean;
  choice?: string;
  freeText?: string;
  timedOut: boolean;
}

export interface HitlChannel {
  pause(request: HitlRequest): Promise<HitlResponse>;
}

// ─── Orchestrator Output ──────────────────────────────────────────

export type DegradationTier = 1 | 2 | 3 | 4;

export interface OrchestratorResponse {
  content: string;
  owlName: string;
  owlEmoji: string;
  toolsUsed: string[];
  qualityScore: number;
  degradationTier: DegradationTier;
  taskCategory?: string;
  complexity: TaskComplexity;
  ledgerId?: string;
  evolutionSignals: {
    qualityScore: number;
    taskCategory: string;
    followUpSentiment?: "positive" | "correction" | "neutral";
  };
}
