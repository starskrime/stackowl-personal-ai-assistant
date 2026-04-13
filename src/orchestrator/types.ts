/**
 * StackOwl — Task Orchestrator Types
 *
 * Defines the strategy taxonomy for intelligent multi-strategy execution.
 * The classifier returns a TaskStrategy, and the orchestrator executes it.
 */

// ─── Strategy Types ──────────────────────────────────────────

export type StrategyType =
  | "DIRECT" // Quick answer, no tools (greetings, trivial facts)
  | "STANDARD" // Single owl with tools (default engine.run())
  | "SPECIALIST" // Route to a specific specialist owl based on domain
  | "PLANNED" // Multi-step with dependency-aware parallel execution
  | "PARLIAMENT" // Multi-owl debate with smart owl selection
  | "SWARM"; // Multiple owls work on different subtasks in parallel

export interface OwlAssignment {
  owlName: string;
  /** Role in the strategy: "lead", "reviewer", "subtask:cost-analysis", etc. */
  role: string;
  /** Why this owl was chosen */
  reasoning: string;
}

export interface SubTask {
  id: number;
  description: string;
  assignedOwl: string;
  dependsOn: number[];
  toolsNeeded: string[];
}

export interface ResearchSignal {
  /** Why deep research mode was triggered */
  reason: string;
  /** Subtopics identified in the research query */
  subtopics: string[];
  /** Whether this was auto-detected vs user-explicit */
  autoDetected: boolean;
}

export interface TaskStrategy {
  strategy: StrategyType;
  /** LLM's explanation of why this strategy was chosen */
  reasoning: string;
  /** 0-1 confidence score */
  confidence: number;
  /** Depth mode: "quick" (default) or "deep" (multi-iteration research) */
  depth: "quick" | "deep";
  /** Research signal — present when depth="deep" */
  researchSignal?: ResearchSignal;
  owlAssignments: OwlAssignment[];
  /** Subtasks for PLANNED and SWARM strategies */
  subtasks?: SubTask[];
  /** Parliament-specific config */
  parliamentConfig?: {
    topic: string;
    owlCount: number;
  };
}

// ─── Execution Result ────────────────────────────────────────

export interface OrchestrationResult {
  content: string;
  owlName: string;
  owlEmoji: string;
  toolsUsed: string[];
  strategy: StrategyType;
  subtaskResults?: Array<{
    id: number;
    owlName: string;
    status: "done" | "failed";
    content: string;
  }>;
  usage?: {
    promptTokens: number;
    completionTokens: number;
  };
}

// ─── Cross-App Action Types ──────────────────────────────────

export interface ActionStep {
  id: string;
  /** Connector preset ID or tool name */
  app: string;
  /** What to do */
  action: string;
  args: Record<string, unknown>;
  dependsOn?: string[];
  /** Fields to extract from result for downstream steps */
  extractFields?: string[];
}

export interface ActionPlan {
  id: string;
  description: string;
  steps: ActionStep[];
  estimatedDuration?: string;
  requiresConfirmation: boolean;
}

export interface ActionResult {
  planId: string;
  status: "completed" | "partial" | "failed";
  stepResults: Array<{
    stepId: string;
    app: string;
    status: "done" | "failed" | "skipped";
    output?: string;
    error?: string;
  }>;
}
