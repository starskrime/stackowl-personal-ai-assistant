/**
 * StackOwl — Goal Types
 *
 * Goals represent the user's high-level objectives that persist
 * across conversations. The GoalGraph tracks their lifecycle and
 * relationships, enabling proactive follow-up and autonomous planning.
 */

// ─── Status ──────────────────────────────────────────────────────

export type GoalStatus =
  | 'active'         // user is working on this
  | 'in_progress'    // partially completed
  | 'blocked'        // waiting on external input or dependency
  | 'completed'      // done
  | 'abandoned'      // user explicitly dropped or went quiet
  | 'deferred';      // user said "later" / "not now"

export type GoalPriority = 'critical' | 'high' | 'medium' | 'low';

// ─── Goal ────────────────────────────────────────────────────────

export interface Goal {
  /** Unique goal identifier */
  id: string;
  /** Short title (e.g., "Launch startup website") */
  title: string;
  /** Detailed description extracted from conversations */
  description: string;
  /** Current status */
  status: GoalStatus;
  /** Priority level */
  priority: GoalPriority;
  /** IDs of sub-goals */
  subGoalIds: string[];
  /** ID of parent goal (if this is a sub-goal) */
  parentId?: string;
  /** IDs of goals that must complete before this one can start */
  dependsOn: string[];
  /** Progress percentage (0-100) */
  progress: number;
  /** What's currently blocking this goal (if status is 'blocked') */
  blockedReason?: string;
  /** Key milestones or deliverables */
  milestones: GoalMilestone[];
  /** Conversation IDs where this goal was discussed */
  mentionedInSessions: string[];
  /** Last time the user mentioned or worked on this goal */
  lastActiveAt: number;
  /** Creation timestamp */
  createdAt: number;
  /** Last update timestamp */
  updatedAt: number;
  /** Tags for categorization */
  tags: string[];
}

// ─── Milestone ───────────────────────────────────────────────────

export interface GoalMilestone {
  id: string;
  description: string;
  completed: boolean;
  completedAt?: number;
}

// ─── Goal Extraction ─────────────────────────────────────────────

/** Result from LLM-based goal extraction from conversation */
export interface GoalExtraction {
  /** New goals detected in the conversation */
  newGoals: Array<{
    title: string;
    description: string;
    priority: GoalPriority;
    milestones: string[];
  }>;
  /** Updates to existing goals (status changes, progress) */
  goalUpdates: Array<{
    goalTitle: string;  // matched by fuzzy title
    statusChange?: GoalStatus;
    progressDelta?: number;
    milestonesCompleted?: string[];
    newBlocker?: string;
  }>;
}
