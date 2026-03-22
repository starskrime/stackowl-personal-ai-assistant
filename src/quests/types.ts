/**
 * StackOwl — Wisdom Quests Types
 *
 * Gamified learning journeys with milestones, adaptive difficulty, and proactive check-ins.
 */

export type QuestStatus = 'active' | 'paused' | 'completed' | 'abandoned';
export type QuestDifficulty = 'beginner' | 'intermediate' | 'advanced' | 'expert';

export interface QuestMilestone {
  id: string;
  title: string;
  description: string;
  /** Criteria to consider this milestone complete */
  completionCriteria: string;
  /** Whether a pellet was created that covers this milestone */
  completed: boolean;
  completedAt?: string;
  /** Related pellet IDs that demonstrate mastery */
  relatedPellets: string[];
  order: number;
}

export interface Quest {
  id: string;
  title: string;
  description: string;
  topic: string;
  difficulty: QuestDifficulty;
  status: QuestStatus;
  milestones: QuestMilestone[];
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
  /** LLM-generated next suggestion */
  nextSuggestion?: string;
  /** Total estimated effort (e.g. "2 weeks") */
  estimatedEffort?: string;
}

export interface QuestProgress {
  questId: string;
  questTitle: string;
  completedMilestones: number;
  totalMilestones: number;
  percentComplete: number;
  nextMilestone?: QuestMilestone;
  suggestion?: string;
}
