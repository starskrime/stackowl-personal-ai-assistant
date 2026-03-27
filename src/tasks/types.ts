/**
 * StackOwl — Task Types
 *
 * Defines the shape of durable task state. Tasks persist across
 * restarts, enabling background execution and resume-on-crash.
 */

// ─── Status ──────────────────────────────────────────────────────

export type TaskStatus =
  | "pending" // queued but not started
  | "running" // actively executing (ReAct loop in progress)
  | "paused" // waiting for user input or approval
  | "completed" // finished successfully
  | "failed" // terminal failure
  | "cancelled"; // user or system cancelled

// ─── Checkpoint ──────────────────────────────────────────────────

/** Snapshot of ReAct loop state for resume-on-crash */
export interface TaskCheckpoint {
  /** Current iteration index in the ReAct loop */
  iteration: number;
  /** Tool calls already executed (fingerprints) */
  completedToolCalls: string[];
  /** Intermediate tool results accumulated so far */
  toolResults: Array<{ toolName: string; result: string; success: boolean }>;
  /** Accumulated content from the LLM so far */
  accumulatedContent: string;
  /** Timestamp of this checkpoint */
  timestamp: number;
}

// ─── Task ────────────────────────────────────────────────────────

export interface Task {
  /** Unique task identifier */
  id: string;
  /** The original user message that spawned this task */
  userMessage: string;
  /** Current execution status */
  status: TaskStatus;
  /** Channel this task originated from (telegram, cli, web) */
  channelId: string;
  /** User who requested this task */
  userId: string;
  /** Session this task belongs to */
  sessionId: string;
  /** Whether this is running in the background (user can send other messages) */
  background: boolean;
  /** Current progress description shown to user */
  progressMessage: string;
  /** Percentage progress estimate (0-100), -1 if unknown */
  progressPercent: number;

  /** The tools used during execution */
  toolsUsed: string[];
  /** Final result content (populated on completion) */
  result?: string;
  /** Error message (populated on failure) */
  error?: string;

  /** Latest ReAct loop checkpoint for resume capability */
  checkpoint?: TaskCheckpoint;
  /** Number of times this task has been retried */
  retryCount: number;
  /** Maximum retries allowed before terminal failure */
  maxRetries: number;

  /** Creation timestamp */
  createdAt: number;
  /** Last update timestamp */
  updatedAt: number;
  /** Completion timestamp */
  completedAt?: number;
}

// ─── Task Events ─────────────────────────────────────────────────

export type TaskEvent =
  | { type: "task:created"; task: Task }
  | { type: "task:started"; taskId: string }
  | { type: "task:progress"; taskId: string; message: string; percent: number }
  | { type: "task:checkpoint"; taskId: string; checkpoint: TaskCheckpoint }
  | { type: "task:completed"; taskId: string; result: string }
  | { type: "task:failed"; taskId: string; error: string }
  | { type: "task:cancelled"; taskId: string };
