/**
 * StackOwl — Intent State Machine
 *
 * Tracks what the user is trying to accomplish RIGHT NOW across messages
 * and sessions. Short-lived (minutes to hours) vs Goals which are long-term.
 *
 * The state machine:
 *   - Creates intents when user messages sound like tasks/requests
 *   - Tracks checkpoints toward completing the intent
 *   - Records owl commitments ("I'll remind you tomorrow")
 *   - Detects stale intents (no activity in 30+ min)
 *   - Feeds into ProactiveIntentionLoop for follow-ups
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type {
  Intent,
  IntentStatus,
  OwlCommitment,
  IntentCheckpoint,
  IntentType,
} from "./types.js";
import { log } from "../logger.js";

const STALE_THRESHOLD_MS = 30 * 60 * 1000;

export class IntentStateMachine {
  private intents: Map<string, Intent> = new Map();
  private filePath: string;
  private loaded = false;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "intents", "state.json");
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data) as Intent[];
        for (const intent of parsed) {
          this.intents.set(intent.id, intent);
        }
        log.engine.info(`[IntentSM] Loaded ${this.intents.size} intents`);
      }
    } catch (err) {
      log.engine.warn(
        `[IntentSM] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;

    // Decay stale threads (>14 days inactive → abandoned)
    this.decayThreads();
  }

  async save(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(
      this.filePath,
      JSON.stringify([...this.intents.values()], null, 2),
      "utf-8",
    );
  }

  create(params: {
    rawQuery: string;
    description: string;
    type: IntentType;
    sessionId: string;
  }): Intent {
    const now = Date.now();
    const intent: Intent = {
      id: `intent_${now}_${Math.random().toString(36).slice(2, 9)}`,
      description: params.description,
      rawQuery: params.rawQuery,
      type: params.type,
      status: "pending",
      checkpoints: [],
      commitments: [],
      sessionId: params.sessionId,
      createdAt: now,
      updatedAt: now,
      lastActiveAt: now,
    };
    this.intents.set(intent.id, intent);
    log.engine.info(
      `[IntentSM] Created: "${params.description}" [${intent.id}]`,
    );
    return intent;
  }

  transition(intentId: string, status: IntentStatus, reason?: string): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    const prev = intent.status;
    intent.status = status;
    intent.updatedAt = Date.now();
    intent.lastActiveAt = Date.now();
    if (status === "blocked" && reason) intent.blockedReason = reason;
    log.engine.info(`[IntentSM] ${intent.id}: ${prev} → ${status}`);
  }

  addCheckpoint(intentId: string, description: string): IntentCheckpoint {
    const intent = this.intents.get(intentId);
    if (!intent) throw new Error(`Intent ${intentId} not found`);
    const cp: IntentCheckpoint = {
      id: `cp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      description,
    };
    intent.checkpoints.push(cp);
    intent.updatedAt = Date.now();
    return cp;
  }

  completeCheckpoint(
    intentId: string,
    checkpointId: string,
    by: "owl" | "user" | "auto",
  ): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    const cp = intent.checkpoints.find((c) => c.id === checkpointId);
    if (cp && !cp.completedAt) {
      cp.completedAt = Date.now();
      cp.completedBy = by;
      intent.updatedAt = Date.now();
      intent.lastActiveAt = Date.now();
    }
    const allDone = intent.checkpoints.every((c) => c.completedAt);
    if (allDone && intent.status === "in_progress") {
      this.transition(intentId, "completed");
    }
  }

  addCommitment(
    intentId: string,
    commitment: Omit<OwlCommitment, "id" | "fulfilled">,
  ): OwlCommitment {
    const intent = this.intents.get(intentId);
    if (!intent) throw new Error(`Intent ${intentId} not found`);
    const c: OwlCommitment = {
      id: `commit_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      fulfilled: false,
      ...commitment,
    };
    intent.commitments.push(c);
    intent.updatedAt = Date.now();
    return c;
  }

  fulfillCommitment(intentId: string, commitmentId: string): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    const c = intent.commitments.find((c) => c.id === commitmentId);
    if (c && !c.fulfilled) {
      c.fulfilled = true;
      c.fulfilledAt = Date.now();
      intent.updatedAt = Date.now();
    }
  }

  getActive(): Intent[] {
    return [...this.intents.values()].filter(
      (i) => !["completed", "abandoned"].includes(i.status),
    );
  }

  getStale(thresholdMs = STALE_THRESHOLD_MS): Intent[] {
    const cutoff = Date.now() - thresholdMs;
    return this.getActive().filter((i) => i.lastActiveAt < cutoff);
  }

  getPendingCommitments(): Array<{
    intent: Intent;
    commitment: OwlCommitment;
  }> {
    const result: Array<{ intent: Intent; commitment: OwlCommitment }> = [];
    for (const intent of this.getActive()) {
      for (const c of intent.commitments) {
        if (!c.fulfilled) result.push({ intent, commitment: c });
      }
    }
    return result;
  }

  getBySession(sessionId: string): Intent[] {
    return [...this.intents.values()].filter((i) => i.sessionId === sessionId);
  }

  getActiveForSession(sessionId: string): Intent | undefined {
    return this.getBySession(sessionId).find(
      (i) => !["completed", "abandoned"].includes(i.status),
    );
  }

  linkToGoal(intentId: string, goalId: string): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    intent.linkedGoalId = goalId;
    intent.updatedAt = Date.now();
  }

  touch(intentId: string): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    intent.lastActiveAt = Date.now();
    intent.updatedAt = Date.now();
  }

  // ─── NarrativeThread methods ──────────────────────────────────

  /**
   * Promote an intent to a cross-session narrative thread.
   * Threads survive session boundaries and are matched by topic.
   */
  promoteToThread(intentId: string, summary: string): void {
    const intent = this.intents.get(intentId);
    if (!intent) return;
    if (intent.isThread) return; // already promoted
    intent.isThread = true;
    intent.summary = summary;
    intent.sessions = [intent.sessionId];
    intent.resumeCount = 0;
    intent.updatedAt = Date.now();
    log.engine.info(
      `[IntentSM] Promoted to thread: "${summary}" [${intentId}]`,
    );
  }

  /**
   * Get all active/paused threads (cross-session narrative threads).
   */
  getActiveThreads(): Intent[] {
    return [...this.intents.values()].filter(
      (i) =>
        i.isThread === true &&
        !["completed", "abandoned"].includes(i.status),
    );
  }

  /**
   * Find a thread whose summary matches the given query.
   * Uses keyword overlap scoring (no LLM, instant).
   * Returns the best match above threshold, or null.
   */
  getThreadForTopic(query: string, threshold = 0.3): Intent | null {
    const threads = this.getActiveThreads();
    if (threads.length === 0) return null;

    const queryWords = new Set(
      query
        .toLowerCase()
        .split(/\W+/)
        .filter((w) => w.length > 2),
    );
    if (queryWords.size === 0) return null;

    let bestMatch: Intent | null = null;
    let bestScore = 0;

    for (const thread of threads) {
      const summaryWords = new Set(
        (thread.summary ?? thread.description)
          .toLowerCase()
          .split(/\W+/)
          .filter((w) => w.length > 2),
      );
      if (summaryWords.size === 0) continue;

      // Jaccard-ish overlap: |intersection| / min(|A|, |B|)
      let overlap = 0;
      for (const w of queryWords) {
        if (summaryWords.has(w)) overlap++;
      }
      const score = overlap / Math.min(queryWords.size, summaryWords.size);

      if (score > bestScore) {
        bestScore = score;
        bestMatch = thread;
      }
    }

    return bestScore >= threshold ? bestMatch : null;
  }

  /**
   * Resume a thread: increment resumeCount, add session, update lastActiveAt.
   */
  resumeThread(intentId: string, sessionId: string): void {
    const intent = this.intents.get(intentId);
    if (!intent || !intent.isThread) return;
    intent.resumeCount = (intent.resumeCount ?? 0) + 1;
    intent.sessions = intent.sessions ?? [];
    if (!intent.sessions.includes(sessionId)) {
      intent.sessions.push(sessionId);
    }
    intent.lastActiveAt = Date.now();
    intent.updatedAt = Date.now();
    if (intent.status === "abandoned" || intent.status === "pending") {
      intent.status = "in_progress";
    }
    log.engine.info(
      `[IntentSM] Thread resumed: "${intent.summary}" (resume #${intent.resumeCount}) [${intentId}]`,
    );
  }

  /**
   * Decay stale threads. Called on load().
   * Threads inactive for >14 days transition to "abandoned".
   */
  decayThreads(maxAgeDays = 14): number {
    const cutoff = Date.now() - maxAgeDays * 24 * 60 * 60 * 1000;
    let decayed = 0;
    for (const intent of this.intents.values()) {
      if (
        intent.isThread &&
        !["completed", "abandoned"].includes(intent.status) &&
        intent.lastActiveAt < cutoff
      ) {
        intent.status = "abandoned";
        intent.updatedAt = Date.now();
        decayed++;
      }
    }
    if (decayed > 0) {
      log.engine.info(`[IntentSM] Decayed ${decayed} stale thread(s)`);
    }
    return decayed;
  }

  toContextString(maxLength = 800): string {
    const active = this.getActive();
    if (active.length === 0) return "";

    const statusIcon: Record<IntentStatus, string> = {
      pending: "⏳",
      in_progress: "🔄",
      waiting_on_user: "👆",
      blocked: "🚫",
      completed: "✅",
      abandoned: "⚪",
    };

    const lines: string[] = ["<active_intents>"];
    for (const intent of active.slice(0, 5)) {
      const checkpointStr =
        intent.checkpoints.length > 0
          ? ` | ${intent.checkpoints.map((c) => (c.completedAt ? "✓" : "○")).join("")} ${intent.checkpoints.length} steps`
          : "";

      const pendingCommitments = intent.commitments.filter(
        (c) => !c.fulfilled,
      ).length;
      const commitStr =
        pendingCommitments > 0
          ? ` | ${pendingCommitments} pending promise(s)`
          : "";

      const blockStr = intent.blockedReason
        ? ` | BLOCKED: ${intent.blockedReason}`
        : "";

      lines.push(
        `  ${statusIcon[intent.status]} ${intent.description}${checkpointStr}${commitStr}${blockStr}`,
      );
    }
    lines.push("</active_intents>");

    // Append narrative threads section if any exist
    const threads = this.getActiveThreads();
    if (threads.length > 0) {
      lines.push("");
      lines.push("<narrative_threads>");
      for (const thread of threads.slice(0, 5)) {
        const sessions = thread.sessions?.length ?? 1;
        const resumes = thread.resumeCount ?? 0;
        const progressStr = thread.progress ? ` | Progress: ${thread.progress}` : "";
        const nextStr = thread.nextSteps?.length
          ? ` | Next: ${thread.nextSteps[0]}`
          : "";
        lines.push(
          `  📌 ${thread.summary ?? thread.description} (${sessions} session(s), resumed ${resumes}x)${progressStr}${nextStr}`,
        );
      }
      lines.push("</narrative_threads>");
    }

    const result = lines.join("\n");
    return result.length > maxLength
      ? result.slice(0, maxLength) + "...[truncated]"
      : result;
  }
}
