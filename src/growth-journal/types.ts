/**
 * StackOwl — Growth Journal Types
 */

export interface JournalEntry {
  id: string;
  period: "weekly" | "monthly";
  startDate: string;
  endDate: string;
  sections: {
    skillsAcquired: string[];
    beliefsChanged: { topic: string; from: string; to: string }[];
    patternsRecognized: string[];
    highlights: string[];
    metrics: GrowthMetrics;
  };
  narrative: string;
  generatedAt: string;
}

export interface GrowthMetrics {
  pelletsCreated: number;
  sessionsCount: number;
  topicsExplored: string[];
  toolsLearned: string[];
  parliamentSessions: number;
  averageSessionLength: number;
}
