/**
 * StackOwl — Goal Graph
 *
 * Persistent goal tracker that remembers what the user is trying to achieve
 * across sessions. Goals have states, sub-goals, dependencies, and milestones.
 *
 * The graph is:
 *   - Extracted automatically from conversations (LLM-based)
 *   - Updated when milestones are completed or blockers appear
 *   - Queried by the ProactivePinger to decide follow-ups
 *   - Injected into the system prompt so the owl has working memory
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type {
  Goal,
  GoalStatus,
  GoalPriority,
  GoalExtraction,
} from "./types.js";
import { log } from "../logger.js";

// ─── Constants ───────────────────────────────────────────────────

/** Goals not mentioned in this many days get flagged as potentially stale */
const STALE_THRESHOLD_DAYS = 7;

// ─── Goal Graph ──────────────────────────────────────────────────

export class GoalGraph {
  private goals: Map<string, Goal> = new Map();
  private filePath: string;
  private loaded = false;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "goals", "goal-graph.json");
  }

  // ─── Persistence ───────────────────────────────────────────────

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data) as Goal[];
        for (const goal of parsed) {
          this.goals.set(goal.id, goal);
        }
      }
    } catch (err) {
      log.engine.warn(
        `[GoalGraph] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) {
      await mkdir(dir, { recursive: true });
    }
    const data = JSON.stringify([...this.goals.values()], null, 2);
    await writeFile(this.filePath, data, "utf-8");
  }

  // ─── CRUD ──────────────────────────────────────────────────────

  addGoal(params: {
    title: string;
    description: string;
    priority: GoalPriority;
    milestones?: string[];
    parentId?: string;
    sessionId?: string;
  }): Goal {
    const now = Date.now();
    const goal: Goal = {
      id: `goal_${now}_${Math.random().toString(36).slice(2, 8)}`,
      title: params.title,
      description: params.description,
      status: "active",
      priority: params.priority,
      subGoalIds: [],
      parentId: params.parentId,
      dependsOn: [],
      progress: 0,
      milestones: (params.milestones ?? []).map((desc, i) => ({
        id: `ms_${now}_${i}`,
        description: desc,
        completed: false,
      })),
      mentionedInSessions: params.sessionId ? [params.sessionId] : [],
      lastActiveAt: now,
      createdAt: now,
      updatedAt: now,
      tags: [],
    };

    this.goals.set(goal.id, goal);

    // Wire to parent if specified
    if (params.parentId) {
      const parent = this.goals.get(params.parentId);
      if (parent) {
        parent.subGoalIds.push(goal.id);
        parent.updatedAt = now;
      }
    }

    log.engine.info(`[GoalGraph] New goal: "${goal.title}" (${goal.priority})`);
    return goal;
  }

  updateGoalStatus(goalId: string, status: GoalStatus, reason?: string): void {
    const goal = this.goals.get(goalId);
    if (!goal) return;

    goal.status = status;
    goal.updatedAt = Date.now();
    if (status === "blocked" && reason) {
      goal.blockedReason = reason;
    }
    if (status === "completed") {
      goal.progress = 100;
    }
  }

  completeMilestone(goalId: string, milestoneDesc: string): void {
    const goal = this.goals.get(goalId);
    if (!goal) return;

    const milestone = goal.milestones.find((m) =>
      m.description.toLowerCase().includes(milestoneDesc.toLowerCase()),
    );
    if (milestone && !milestone.completed) {
      milestone.completed = true;
      milestone.completedAt = Date.now();
      goal.updatedAt = Date.now();

      // Recalculate progress
      const total = goal.milestones.length;
      const done = goal.milestones.filter((m) => m.completed).length;
      goal.progress = total > 0 ? Math.round((done / total) * 100) : 0;

      log.engine.info(
        `[GoalGraph] Milestone completed: "${milestoneDesc}" on "${goal.title}" — ${goal.progress}%`,
      );
    }
  }

  recordMention(goalId: string, sessionId: string): void {
    const goal = this.goals.get(goalId);
    if (!goal) return;

    goal.lastActiveAt = Date.now();
    goal.updatedAt = Date.now();
    if (!goal.mentionedInSessions.includes(sessionId)) {
      goal.mentionedInSessions.push(sessionId);
    }
  }

  // ─── Queries ───────────────────────────────────────────────────

  getAll(): Goal[] {
    return [...this.goals.values()];
  }

  getActive(): Goal[] {
    return this.getAll().filter(
      (g) => g.status === "active" || g.status === "in_progress",
    );
  }

  getBlocked(): Goal[] {
    return this.getAll().filter((g) => g.status === "blocked");
  }

  /**
   * Goals that haven't been mentioned recently — candidates for follow-up.
   */
  getStale(daysThreshold: number = STALE_THRESHOLD_DAYS): Goal[] {
    const cutoff = Date.now() - daysThreshold * 24 * 60 * 60 * 1000;
    return this.getActive().filter((g) => g.lastActiveAt < cutoff);
  }

  /**
   * Find a goal by fuzzy title match.
   */
  findByTitle(titleQuery: string): Goal | undefined {
    const lower = titleQuery.toLowerCase();
    return this.getAll().find((g) => g.title.toLowerCase().includes(lower));
  }

  /**
   * Get the highest-priority active goal.
   */
  getTopPriority(): Goal | undefined {
    const priorityOrder: GoalPriority[] = ["critical", "high", "medium", "low"];
    const active = this.getActive();
    for (const p of priorityOrder) {
      const found = active.find((g) => g.priority === p);
      if (found) return found;
    }
    return active[0];
  }

  // ─── Context Injection ─────────────────────────────────────────

  /**
   * Format active goals for injection into the system prompt.
   * Concise format that gives the owl awareness of user's objectives.
   */
  toContextString(): string {
    const active = this.getActive();
    if (active.length === 0) return "";

    const lines = ["<user_goals>"];
    for (const goal of active.slice(0, 8)) {
      const milestoneStr =
        goal.milestones.length > 0
          ? ` | Milestones: ${goal.milestones.map((m) => `${m.completed ? "✓" : "○"} ${m.description}`).join(", ")}`
          : "";
      const blockerStr =
        goal.status === "blocked" && goal.blockedReason
          ? ` | BLOCKED: ${goal.blockedReason}`
          : "";
      lines.push(
        `  [${goal.priority.toUpperCase()}] ${goal.title} — ${goal.progress}% complete${milestoneStr}${blockerStr}`,
      );
    }
    lines.push("</user_goals>");
    return lines.join("\n");
  }

  // ─── LLM-based Extraction ─────────────────────────────────────

  /**
   * Extract goals and updates from a conversation using the LLM.
   */
  async extractFromConversation(
    messages: ChatMessage[],
    provider: ModelProvider,
    sessionId: string,
  ): Promise<void> {
    await this.load();

    const userMessages = messages
      .filter((m) => m.role === "user")
      .map((m) => (m.content ?? "").slice(0, 300))
      .join("\n");

    if (userMessages.length < 20) return; // Too short to extract goals

    const existingGoals = this.getAll()
      .map((g) => `- "${g.title}" [${g.status}] ${g.progress}%`)
      .join("\n");

    const systemPrompt =
      `You analyze conversations to extract user goals.` +
      `\nExisting goals:\n${existingGoals || "(none)"}` +
      `\n\nOutput valid JSON matching this schema:` +
      `\n{ "newGoals": [{ "title": string, "description": string, "priority": "critical"|"high"|"medium"|"low", "milestones": string[] }],` +
      `  "goalUpdates": [{ "goalTitle": string, "statusChange"?: string, "progressDelta"?: number, "milestonesCompleted"?: string[] }] }` +
      `\n\nRules:` +
      `\n- Only create a new goal if the user expresses a genuine multi-step objective (not a single question)` +
      `\n- Update existing goals if the conversation shows progress or new blockers` +
      `\n- Be conservative — don't over-extract. Empty arrays are fine.` +
      `\nOutput ONLY valid JSON.`;

    try {
      const response = await provider.chat(
        [
          { role: "system", content: systemPrompt },
          { role: "user", content: `Conversation:\n${userMessages}` },
        ],
        undefined,
        { temperature: 0, maxTokens: 512 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```(?:json)?/, "")
          .replace(/```$/, "")
          .trim();
      }

      const extraction = JSON.parse(jsonStr) as GoalExtraction;

      // Apply new goals
      for (const newGoal of extraction.newGoals ?? []) {
        // Check for duplicates
        const existing = this.findByTitle(newGoal.title);
        if (!existing) {
          this.addGoal({
            title: newGoal.title,
            description: newGoal.description,
            priority: newGoal.priority,
            milestones: newGoal.milestones,
            sessionId,
          });
        }
      }

      // Apply updates
      for (const update of extraction.goalUpdates ?? []) {
        const goal = this.findByTitle(update.goalTitle);
        if (!goal) continue;

        if (update.statusChange) {
          this.updateGoalStatus(goal.id, update.statusChange as GoalStatus);
        }
        if (update.progressDelta) {
          goal.progress = Math.min(100, goal.progress + update.progressDelta);
          goal.updatedAt = Date.now();
        }
        for (const ms of update.milestonesCompleted ?? []) {
          this.completeMilestone(goal.id, ms);
        }
        this.recordMention(goal.id, sessionId);
      }

      await this.save();
    } catch (err) {
      log.engine.warn(
        `[GoalGraph] Extraction failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
}
