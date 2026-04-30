// src/routing/relationship-context.ts
import type { MemoryDatabase } from "../memory/db.js";
import type { GoalGraph } from "../goals/graph.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { UserMemoryStore } from "../session/user-memory-store.js";

export interface RelationshipSummary {
  communicationStyle: string;
  expertiseLevel: string;
  recurringTopics: string[];
  openCommitments: string[];
  lastInteraction: string;
}

export class RelationshipContext {
  constructor(
    private db: Pick<MemoryDatabase, "userProfiles" | "owlTasks">,
    private goalGraph: GoalGraph | undefined,       // reserved for Task 11
    private episodicMemory: EpisodicMemory | undefined, // reserved for Task 11
    private userMemoryStore: UserMemoryStore | undefined,
  ) {
    void this.goalGraph;
    void this.episodicMemory;
  }

  async buildSummary(userId: string): Promise<RelationshipSummary> {
    const tasks = this.db.owlTasks.getActive(userId);
    const openCommitments = tasks.map((t) => t.title);

    const history = this.db.userProfiles.getRoutingHistory(userId);
    const owlFreq: Record<string, number> = {};
    for (const h of history) { owlFreq[h.owl] = (owlFreq[h.owl] ?? 0) + 1; }
    const recurringTopics = Object.entries(owlFreq)
      .filter(([owl, count]) => count >= 2 && owl !== "noctua" && owl !== "parliament" && owl !== "coordinator")
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([owl]) => owl);

    const lastHistory = history.at(-1);
    const lastInteraction = lastHistory?.ts ?? "unknown";

    let communicationStyle = "unknown";
    let expertiseLevel = "unknown";
    if (this.userMemoryStore) {
      try {
        const styleFacts = await this.userMemoryStore.retrieve(userId, "communication style preference", 1);
        if (styleFacts.length > 0) communicationStyle = styleFacts[0];
        const expertFacts = await this.userMemoryStore.retrieve(userId, "programming expertise level", 1);
        if (expertFacts.length > 0) expertiseLevel = expertFacts[0];
      } catch { /* non-critical */ }
    }

    return { communicationStyle, expertiseLevel, recurringTopics, openCommitments, lastInteraction };
  }

  async buildPromptBlock(userId: string): Promise<string> {
    const summary = await this.buildSummary(userId);
    const parts: string[] = [];

    if (summary.communicationStyle !== "unknown") {
      parts.push(`Style: ${summary.communicationStyle}`);
    }
    if (summary.expertiseLevel !== "unknown") {
      parts.push(`Expertise: ${summary.expertiseLevel}`);
    }
    if (summary.recurringTopics.length > 0) {
      parts.push(`Recurring: ${summary.recurringTopics.join(", ")}`);
    }
    if (summary.openCommitments.length > 0) {
      parts.push(`Open commitments: ${summary.openCommitments.join("; ")}`);
    }

    if (parts.length === 0) return "";
    return `<user_relationship>\n${parts.join("\n")}\n</user_relationship>`;
  }
}
