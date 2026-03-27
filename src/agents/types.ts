/**
 * StackOwl — Agent Abstraction Layer
 *
 * Defines the interface for spawning and managing sub-agents.
 * Agents are autonomous workers that can be backed by different AI systems
 * (OwlEngine, Claude Code, Codex, local LLM, etc.).
 *
 * This is the contract — implementations come later.
 */

// ─── Task & Result ──────────────────────────────────────────────

export type AgentStatus =
  | "idle"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface AgentTask {
  id: string;
  description: string;
  input: string;
  /** Additional context (file paths, prior results, etc.) */
  context?: Record<string, unknown>;
  constraints?: {
    /** Max execution time in milliseconds */
    maxDurationMs?: number;
    /** Max token budget for this task */
    maxTokenBudget?: number;
    /** Restrict which tools the agent can use */
    allowedTools?: string[];
    /** Working directory for file operations */
    workDir?: string;
  };
}

export interface AgentResult {
  taskId: string;
  status: "completed" | "failed";
  output: string;
  toolsUsed: string[];
  tokenUsage?: { promptTokens: number; completionTokens: number };
  durationMs: number;
  /** Files created or modified */
  filesChanged?: string[];
  error?: string;
}

// ─── Agent Interface ────────────────────────────────────────────

export interface CodingAgent {
  readonly id: string;
  readonly name: string;
  /** What this agent is good at: "architecture", "testing", "refactoring", etc. */
  readonly capabilities: string[];
  /** Whether this agent is currently available (health check, API key, etc.) */
  isAvailable(): Promise<boolean>;

  /** Spawn a task and return its ID. Non-blocking. */
  spawn(task: AgentTask): Promise<string>;
  /** Get current status of a task. */
  getStatus(taskId: string): AgentStatus;
  /** Wait for and return the result. Resolves when task completes or fails. */
  getResult(taskId: string): Promise<AgentResult | null>;
  /** Cancel a running task. Returns true if cancellation was successful. */
  cancel(taskId: string): Promise<boolean>;

  /** Optional: stream intermediate progress messages */
  onProgress?(taskId: string, callback: (msg: string) => void): void;

  /** ACP capabilities this agent advertises */
  acpCapabilities?: import("../acp/types.js").ACPCapability[];
  /** Handle an incoming ACP message */
  onMessage?(message: import("../acp/types.js").ACPMessage): Promise<unknown>;
}

// ─── Agent Registry ─────────────────────────────────────────────

export interface AgentRegistry {
  register(agent: CodingAgent): void;
  unregister(id: string): void;
  get(id: string): CodingAgent | undefined;
  list(): CodingAgent[];
  /** Find agents whose capabilities match the given keywords */
  findByCapability(...capabilities: string[]): CodingAgent[];
}
