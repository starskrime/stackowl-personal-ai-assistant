// src/routing/task-ownership-manager.ts
import type { MemoryDatabase, OwlTask, OwlTaskPriority } from "../memory/db.js";
import { v4 as uuidv4 } from "uuid";

const COMMITMENT_PATTERNS = [
  /\bi'?ll\s+(follow\s+up|remind|check\s+back|research|look\s+into|handle|take\s+care|investigate|get\s+back)/i,
  /\bi\s+will\s+(follow\s+up|remind|check\s+back|research|look\s+into|handle|investigate)/i,
  /let\s+me\s+(follow\s+up|check|research|look\s+into|investigate)/i,
  /i'?ll\s+(get\s+that|do\s+that|sort\s+that|fix\s+that)/i,
];

export class TaskOwnershipManager {
  constructor(private db: Pick<MemoryDatabase, "owlTasks">) {}

  createTask(
    userId: string,
    owlName: string,
    title: string,
    description: string | undefined,
    priority: OwlTaskPriority,
    sessionId?: string,
    dueAt?: string,
  ): string {
    const id = uuidv4();
    this.db.owlTasks.create({ id, userId, owlName, title, description, status: "pending", priority, sessionId, dueAt });
    return id;
  }

  markDone(taskId: string, result: string): void {
    this.db.owlTasks.updateStatus(taskId, "done", result);
  }

  markBlocked(taskId: string): void {
    this.db.owlTasks.updateStatus(taskId, "blocked");
  }

  getActiveTasks(userId: string): OwlTask[] {
    return this.db.owlTasks.getActive(userId);
  }

  detectAndCreate(
    userId: string,
    owlName: string,
    sessionId: string,
    responseText: string,
  ): string | null {
    for (const pattern of COMMITMENT_PATTERNS) {
      const match = responseText.match(pattern);
      if (match) {
        const matchIndex = match.index!;
        const snippet = responseText.slice(matchIndex, matchIndex + 80);
        const title = (snippet.replace(/[^a-z0-9 ]/gi, " ").replace(/\s+/g, " ").slice(0, 60).trim()) || match[0].slice(0, 60);
        return this.createTask(userId, owlName, title, undefined, "normal", sessionId);
      }
    }
    return null;
  }

  buildPromptBlock(userId: string): string {
    const tasks = this.getActiveTasks(userId);
    if (tasks.length === 0) return "";
    const lines = tasks.map((t) => {
      const due = t.dueAt ? ` (due: ${t.dueAt.slice(0, 10)})` : "";
      return `- [${t.priority}] ${t.title}${due}`;
    });
    return `<open_tasks>\n${lines.join("\n")}\n</open_tasks>`;
  }
}
