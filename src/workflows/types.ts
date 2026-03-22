/**
 * StackOwl — Workflow Chain Types
 *
 * Multi-step reusable automation chains that extend the skill system.
 * A workflow is a sequence of steps with conditionals, variable passing,
 * and retry logic.
 */

// ─── Step Types ──────────────────────────────────────────────

export type StepType = "tool" | "llm" | "condition" | "parallel" | "wait";

export interface WorkflowStep {
  id: string;
  name: string;
  type: StepType;
  /** Step-specific configuration */
  config: ToolStepConfig | LlmStepConfig | ConditionStepConfig | ParallelStepConfig | WaitStepConfig;
  /** Steps that must complete before this one */
  dependsOn?: string[];
  /** Max retries on failure. Default: 0 */
  retries?: number;
  /** Timeout in ms. Default: 30000 */
  timeoutMs?: number;
  /** Variable mappings: { stepVarName: "previousStep.outputField" } */
  inputs?: Record<string, string>;
  /** What to extract from results for downstream steps */
  outputs?: string[];
}

export interface ToolStepConfig {
  toolName: string;
  args: Record<string, unknown>;
}

export interface LlmStepConfig {
  prompt: string;
  /** Extract structured data from LLM response */
  extractAs?: "json" | "text" | "list";
}

export interface ConditionStepConfig {
  /** Expression to evaluate: "{{stepId.output}} === 'value'" */
  expression: string;
  /** Step to jump to if true */
  thenStep: string;
  /** Step to jump to if false */
  elseStep?: string;
}

export interface ParallelStepConfig {
  /** Steps to run concurrently */
  steps: string[];
}

export interface WaitStepConfig {
  /** Duration in ms */
  durationMs: number;
}

// ─── Workflow Definition ─────────────────────────────────────

export interface WorkflowDefinition {
  id: string;
  name: string;
  description: string;
  /** Trigger phrase(s) that activate this workflow */
  triggers: string[];
  /** Input parameters the user must provide */
  parameters: WorkflowParameter[];
  /** Ordered steps */
  steps: WorkflowStep[];
  /** Who created this: "user" | "mined" | "synthesized" */
  source: "user" | "mined" | "synthesized";
  /** Tags for categorization */
  tags: string[];
  createdAt: number;
  lastRunAt?: number;
  runCount: number;
}

export interface WorkflowParameter {
  name: string;
  description: string;
  type: "string" | "number" | "boolean";
  required: boolean;
  default?: unknown;
}

// ─── Execution ───────────────────────────────────────────────

export type StepStatus = "pending" | "running" | "completed" | "failed" | "skipped";

export interface StepResult {
  stepId: string;
  status: StepStatus;
  output?: unknown;
  error?: string;
  durationMs: number;
  retryCount: number;
}

export interface WorkflowRun {
  id: string;
  workflowId: string;
  status: "running" | "completed" | "failed" | "cancelled";
  parameters: Record<string, unknown>;
  stepResults: StepResult[];
  startedAt: number;
  completedAt?: number;
  error?: string;
}
