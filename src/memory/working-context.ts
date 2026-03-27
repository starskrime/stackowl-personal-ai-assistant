/**
 * StackOwl — Working Context
 *
 * Layer 1 of the memory hierarchy: what is happening RIGHT NOW
 * in the current conversation thread.
 *
 * This is per-session in-memory state that:
 *   - Tracks the current topic being discussed
 *   - Tracks what the owl is actively trying to do for the user
 *   - Stores intermediate results from multi-step tasks
 *   - Provides context for the next exchange
 */

export interface WorkingMemoryEntry {
  key: string;
  value: unknown;
  updatedAt: number;
}

export class WorkingContext {
  private memory: Map<string, WorkingMemoryEntry> = new Map();

  set(key: string, value: unknown): void {
    this.memory.set(key, { key, value, updatedAt: Date.now() });
  }

  get<T>(key: string, defaultVal?: T): T | undefined {
    const entry = this.memory.get(key);
    return (entry?.value as T) ?? defaultVal;
  }

  has(key: string): boolean {
    return this.memory.has(key);
  }

  delete(key: string): void {
    this.memory.delete(key);
  }

  clear(): void {
    this.memory.clear();
  }

  setCurrentTopic(topic: string): void {
    this.set("currentTopic", topic);
    this.set("topicSetAt", Date.now());
  }

  getCurrentTopic(): string | undefined {
    return this.get<string>("currentTopic");
  }

  setActiveIntent(intentId: string): void {
    this.set("activeIntentId", intentId);
  }

  getActiveIntentId(): string | undefined {
    return this.get<string>("activeIntentId");
  }

  setTaskInProgress(taskDescription: string): void {
    this.set("taskInProgress", taskDescription);
    this.set("taskStartedAt", Date.now());
  }

  getTaskInProgress(): string | undefined {
    return this.get<string>("taskInProgress");
  }

  clearTaskInProgress(): void {
    this.delete("taskInProgress");
    this.delete("taskStartedAt");
  }

  setLastUserMessage(msg: string): void {
    this.set("lastUserMessage", msg);
    this.set("lastUserMessageAt", Date.now());
  }

  getLastUserMessage(): string | undefined {
    return this.get<string>("lastUserMessage");
  }

  setLastOwlResponse(msg: string): void {
    this.set("lastOwlResponse", msg);
  }

  getLastOwlResponse(): string | undefined {
    return this.get<string>("lastOwlResponse");
  }

  /**
   * Check if the conversation topic has been the same for too long
   * (potential loop or stuck situation)
   */
  isTopicStale(thresholdMs = 5 * 60 * 1000): boolean {
    const topicSetAt = this.get<number>("topicSetAt");
    if (!topicSetAt) return false;
    return Date.now() - topicSetAt > thresholdMs;
  }

  toContextString(): string {
    if (this.memory.size === 0) return "";

    const entries: string[] = [];
    const topic = this.getCurrentTopic();
    if (topic) entries.push(`current_topic: ${topic}`);

    const task = this.getTaskInProgress();
    if (task) entries.push(`active_task: ${task}`);

    const lastMsg = this.getLastUserMessage();
    if (lastMsg) entries.push(`last_user_message: ${lastMsg.slice(0, 100)}`);

    if (entries.length === 0) return "";
    return `<working_context>\n  ${entries.join("\n  ")}\n</working_context>`;
  }

  toDebugString(): string {
    const entries = [...this.memory.entries()].map(
      ([k, v]) => `${k}: ${JSON.stringify(v.value).slice(0, 50)}`,
    );
    return entries.join(" | ");
  }
}

/**
 * Manages per-session WorkingContext instances.
 * Cleans up contexts when sessions expire.
 */
export class WorkingContextManager {
  private contexts: Map<string, WorkingContext> = new Map();

  getOrCreate(sessionId: string): WorkingContext {
    let ctx = this.contexts.get(sessionId);
    if (!ctx) {
      ctx = new WorkingContext();
      this.contexts.set(sessionId, ctx);
    }
    return ctx;
  }

  get(sessionId: string): WorkingContext | undefined {
    return this.contexts.get(sessionId);
  }

  delete(sessionId: string): void {
    this.contexts.delete(sessionId);
  }

  size(): number {
    return this.contexts.size;
  }
}
