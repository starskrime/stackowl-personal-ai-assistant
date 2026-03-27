/**
 * StackOwl — Intent Types
 *
 * Intents track what the user is trying to accomplish right now,
 * across messages and sessions. Intents are short-lived (minutes to hours)
 * compared to Goals which are long-term (days to months).
 *
 * An Intent can be "promoted" to a Goal when the user commits to pursuing it.
 */

export type IntentStatus =
  | "pending" // user asked, owl acknowledged, not started yet
  | "in_progress" // owl is actively working on this
  | "waiting_on_user" // owl needs input from user to continue
  | "blocked" // waiting on external dependency (API, person, system)
  | "completed" // task done, user confirmed or auto-detected
  | "abandoned"; // user explicitly gave up or moved on

export type IntentType =
  | "task" // user wants something done (book a room, write code)
  | "question" // user wants an answer
  | "information" // user wants to find out something
  | "relationship" // social/emotional interaction
  | "exploration"; // open-ended curiosity

export interface IntentCheckpoint {
  id: string;
  description: string;
  completedAt?: number;
  completedBy?: "owl" | "user" | "auto";
}

export interface OwlCommitment {
  id: string;
  statement: string;
  madeAt: number;
  deadline?: number;
  fulfilled: boolean;
  fulfilledAt?: number;
  followUpMessage: string;
  triggerType: "deadline" | "time_delay" | "context_change";
}

export interface Intent {
  id: string;
  description: string;
  rawQuery: string;
  type: IntentType;
  status: IntentStatus;
  checkpoints: IntentCheckpoint[];
  commitments: OwlCommitment[];
  blockedReason?: string;
  sessionId: string;
  createdAt: number;
  updatedAt: number;
  lastActiveAt: number;
  linkedGoalId?: string;
}
