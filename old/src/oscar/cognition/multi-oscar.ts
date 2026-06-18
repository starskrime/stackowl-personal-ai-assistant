import type { CanonicalAction } from "../types.js";

export interface OscarAgent {
  id: string;
  name: string;
  role: "lead" | "technical" | "reviewer" | "specialist";
  specialty?: string;
  capabilities: string[];
  active: boolean;
}

export interface ParliamentTask {
  id: string;
  description: string;
  subtasks: Subtask[];
  assignedAgents: string[];
  status: "pending" | "in_progress" | "completed" | "failed";
  result?: ParliamentResult;
  createdAt: number;
  completedAt?: number;
}

export interface Subtask {
  id: string;
  description: string;
  assignedAgent: string;
  status: "pending" | "in_progress" | "completed" | "failed";
  result?: unknown;
  actions: CanonicalAction[];
}

export interface ParliamentResult {
  success: boolean;
  actions: CanonicalAction[];
  insights: string[];
  verified: boolean;
  confidence: number;
}

export interface SharedKnowledge {
  facts: Map<string, KnowledgeFact>;
  draftSections: Map<string, string>;
  verificationResults: Map<string, boolean>;
}

export interface KnowledgeFact {
  id: string;
  content: string;
  source: string;
  confidence: number;
  timestamp: number;
}

export class MultiOscarParliament {
  private agents: Map<string, OscarAgent> = new Map();
  private tasks: Map<string, ParliamentTask> = new Map();
  private sharedKnowledge: SharedKnowledge = {
    facts: new Map(),
    draftSections: new Map(),
    verificationResults: new Map(),
  };


  constructor() {
    this.initializeDefaultAgents();
  }

  private initializeDefaultAgents(): void {
    this.registerAgent({
      id: "noctua",
      name: "Noctua",
      role: "lead",
      capabilities: ["coordination", "planning", "user_communication"],
      active: true,
    });

    this.registerAgent({
      id: "archimedes",
      name: "Archimedes",
      role: "technical",
      specialty: "data_processing",
      capabilities: ["data_extraction", "calculation", "formatting"],
      active: true,
    });

    this.registerAgent({
      id: "minerva",
      name: "Minerva",
      role: "reviewer",
      specialty: "quality_assurance",
      capabilities: ["verification", "validation", "error_detection"],
      active: true,
    });
  }

  registerAgent(agent: OscarAgent): void {
    this.agents.set(agent.id, agent);
  }

  unregisterAgent(agentId: string): boolean {
    return this.agents.delete(agentId);
  }

  getAgent(agentId: string): OscarAgent | undefined {
    return this.agents.get(agentId);
  }

  getActiveAgents(): OscarAgent[] {
    return Array.from(this.agents.values()).filter((a) => a.active);
  }

  async createTask(description: string, subtaskDescriptions: string[]): Promise<ParliamentTask> {
    const taskId = `task_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

    const subtasks: Subtask[] = subtaskDescriptions.map((desc, idx) => ({
      id: `subtask_${idx}`,
      description: desc,
      assignedAgent: this.selectAgent(desc),
      status: "pending",
      actions: [],
    }));

    const task: ParliamentTask = {
      id: taskId,
      description,
      subtasks,
      assignedAgents: subtasks.map((s) => s.assignedAgent),
      status: "pending",
      createdAt: Date.now(),
    };

    this.tasks.set(taskId, task);
    return task;
  }

  private selectAgent(subtaskDescription: string): string {
    const activeAgents = this.getActiveAgents();
    const description = subtaskDescription.toLowerCase();

    if (description.includes("extract") || description.includes("calculate")) {
      const technical = activeAgents.find((a) => a.role === "technical");
      if (technical) return technical.id;
    }

    if (description.includes("verify") || description.includes("check")) {
      const reviewer = activeAgents.find((a) => a.role === "reviewer");
      if (reviewer) return reviewer.id;
    }

    return activeAgents[0]?.id || "noctua";
  }

  async executeTask(taskId: string): Promise<ParliamentResult | null> {
    const task = this.tasks.get(taskId);
    if (!task) return null;

    task.status = "in_progress";

    const results: unknown[] = [];

    for (const subtask of task.subtasks) {
      subtask.status = "in_progress";

      const result = await this.executeSubtask(subtask);
      subtask.result = result;
      subtask.status = result ? "completed" : "failed";

      if (result) {
        results.push(result);
      }
    }

    const allSucceeded = task.subtasks.every((s) => s.status === "completed");

    task.status = allSucceeded ? "completed" : "failed";
    task.completedAt = Date.now();

    const combinedActions = task.subtasks.flatMap((s) => s.actions);
    const insights = this.extractInsights(task);

    task.result = {
      success: allSucceeded,
      actions: combinedActions,
      insights,
      verified: allSucceeded,
      confidence: this.calculateConfidence(task),
    };

    return task.result;
  }

  private async executeSubtask(subtask: Subtask): Promise<unknown> {
    await this.delay(100);
    return { success: true, message: `Executed: ${subtask.description}` };
  }

  private extractInsights(task: ParliamentTask): string[] {
    const insights: string[] = [];

    for (const subtask of task.subtasks) {
      if (subtask.result && typeof subtask.result === "object") {
        const result = subtask.result as Record<string, unknown>;
        if (result.message) {
          insights.push(String(result.message));
        }
      }
    }

    return insights;
  }

  private calculateConfidence(task: ParliamentTask): number {
    const completedCount = task.subtasks.filter(
      (s) => s.status === "completed"
    ).length;
    return completedCount / task.subtasks.length;
  }

  addKnowledgeFact(fact: Omit<KnowledgeFact, "id" | "timestamp">): KnowledgeFact {
    const id = `fact_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const fullFact: KnowledgeFact = {
      ...fact,
      id,
      timestamp: Date.now(),
    };
    this.sharedKnowledge.facts.set(id, fullFact);
    return fullFact;
  }

  getKnowledgeFacts(): KnowledgeFact[] {
    return Array.from(this.sharedKnowledge.facts.values()).sort(
      (a, b) => b.timestamp - a.timestamp
    );
  }

  setDraftSection(key: string, content: string): void {
    this.sharedKnowledge.draftSections.set(key, content);
  }

  getDraftSection(key: string): string | undefined {
    return this.sharedKnowledge.draftSections.get(key);
  }

  verifyFact(factId: string, verified: boolean): void {
    this.sharedKnowledge.verificationResults.set(factId, verified);
  }

  getTask(taskId: string): ParliamentTask | undefined {
    return this.tasks.get(taskId);
  }

  getActiveTasks(): ParliamentTask[] {
    return Array.from(this.tasks.values())
      .filter((t) => t.status === "in_progress" || t.status === "pending");
  }

  getTaskStats(): {
    total: number;
    byStatus: Record<string, number>;
    activeAgents: number;
    knowledgeFacts: number;
  } {
    const tasks = Array.from(this.tasks.values());

    return {
      total: tasks.length,
      byStatus: {
        pending: tasks.filter((t) => t.status === "pending").length,
        in_progress: tasks.filter((t) => t.status === "in_progress").length,
        completed: tasks.filter((t) => t.status === "completed").length,
        failed: tasks.filter((t) => t.status === "failed").length,
      },
      activeAgents: this.getActiveAgents().length,
      knowledgeFacts: this.sharedKnowledge.facts.size,
    };
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

export const multiOscarParliament = new MultiOscarParliament();
