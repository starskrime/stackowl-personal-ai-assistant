/**
 * StackOwl — Cross-Session Store
 *
 * Persists critical user data across session restarts.
 * Stores preferences, commitments, and important facts durably.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { log } from "../logger.js";
import type { FactStore, StoredFact } from "./fact-store.js";
import type { SessionStore, Session } from "./store.js";

export interface PersistedCommitment {
  id: string;
  description: string;
  createdAt: string;
  completedAt?: string;
  status: "pending" | "in_progress" | "completed" | "cancelled";
  sourceSessionId?: string;
}

export interface CrossSessionData {
  version: number;
  commitments: PersistedCommitment[];
  criticalFacts: StoredFact[];
  loadedAt: string;
}

export class CrossSessionStore {
  private filePath: string;
  private data: CrossSessionData | null = null;
  private loaded = false;

  constructor(
    private _workspacePath: string,
    private factStore?: FactStore,
    private sessionStore?: SessionStore,
  ) {
    this.filePath = join(this._workspacePath, "memory", "cross-session.json");
  }

  /**
   * Load cross-session data from disk
   */
  async load(): Promise<void> {
    if (this.loaded) return;

    try {
      if (existsSync(this.filePath)) {
        const content = await readFile(this.filePath, "utf-8");
        this.data = JSON.parse(content) as CrossSessionData;
        log.engine.info(
          `[CrossSessionStore] Loaded ${this.data.commitments.length} commitments, ${this.data.criticalFacts.length} critical facts`,
        );
      } else {
        this.data = this.emptyData();
      }
    } catch (err) {
      log.engine.warn(
        `[CrossSessionStore] Failed to load: ${err instanceof Error ? err.message : err}. Starting fresh.`,
      );
      this.data = this.emptyData();
    }

    this.loaded = true;
  }

  /**
   * Save cross-session data to disk
   */
  async save(): Promise<void> {
    if (!this.data) {
      this.data = this.emptyData();
    }

    this.data.loadedAt = new Date().toISOString();

    try {
      const dir = dirname(this.filePath);
      if (!existsSync(dir)) {
        await mkdir(dir, { recursive: true });
      }
      await writeFile(this.filePath, JSON.stringify(this.data, null, 2), "utf-8");
      log.engine.debug("[CrossSessionStore] Saved cross-session data");
    } catch (err) {
      log.engine.warn(
        `[CrossSessionStore] Failed to save: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /**
   * Add a commitment
   */
  async addCommitment(
    description: string,
    sourceSessionId?: string,
  ): Promise<PersistedCommitment> {
    await this.load();

    const commitment: PersistedCommitment = {
      id: `commit_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      description,
      createdAt: new Date().toISOString(),
      status: "pending",
      sourceSessionId,
    };

    this.data!.commitments.push(commitment);
    await this.save();

    log.engine.info(`[CrossSessionStore] Added commitment: "${description.slice(0, 50)}"`);
    return commitment;
  }

  /**
   * Update commitment status
   */
  async updateCommitmentStatus(
    id: string,
    status: PersistedCommitment["status"],
  ): Promise<boolean> {
    await this.load();

    const commitment = this.data!.commitments.find((c) => c.id === id);
    if (!commitment) return false;

    commitment.status = status;
    if (status === "completed") {
      commitment.completedAt = new Date().toISOString();
    }

    await this.save();
    return true;
  }

  /**
   * Get all active commitments
   */
  async getActiveCommitments(): Promise<PersistedCommitment[]> {
    await this.load();

    return this.data!.commitments.filter(
      (c) => c.status === "pending" || c.status === "in_progress",
    );
  }

  /**
   * Get all commitments
   */
  async getAllCommitments(): Promise<PersistedCommitment[]> {
    await this.load();
    return [...this.data!.commitments];
  }

  /**
   * Add a critical fact
   */
  async addCriticalFact(fact: Omit<StoredFact, "id" | "createdAt" | "updatedAt" | "accessCount">): Promise<StoredFact> {
    await this.load();

    const storedFact: StoredFact = {
      ...fact,
      id: `cfact_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      accessCount: 0,
    };

    this.data!.criticalFacts.push(storedFact);
    await this.save();

    return storedFact;
  }

  /**
   * Get critical facts
   */
  async getCriticalFacts(): Promise<StoredFact[]> {
    await this.load();
    return [...this.data!.criticalFacts];
  }

  /**
   * Extract and persist critical facts from a session
   */
  async extractFromSession(session: Session): Promise<void> {
    if (!this.factStore) {
      log.engine.debug("[CrossSessionStore] No FactStore configured, skipping extraction");
      return;
    }

    const userMessages = session.messages
      .filter((m) => m.role === "user")
      .map((m) => m.content);

    for (const msg of userMessages) {
      await this.extractFactsFromMessage(msg);
    }
  }

  /**
   * Extract facts from a message
   */
  private async extractFactsFromMessage(message: string): Promise<void> {
    if (!this.factStore) return;

    const commitmentPatterns = [
      /remember to/i,
      /don't forget to/i,
      /make sure to/i,
      /i want you to/i,
      /i need you to/i,
      /will do/i,
      /will remember/i,
    ];

    const decisionPatterns = [
      /decided to/i,
      /we agreed/i,
      /i'll use/i,
      /i'll go with/i,
      /the approach is/i,
    ];

    const isCommitment = commitmentPatterns.some((p) => p.test(message));
    const isDecision = decisionPatterns.some((p) => p.test(message));

    if (isCommitment || isDecision) {
      const category = isCommitment ? "goal" : "decision";
      await this.factStore.add({
        userId: "default",
        fact: message.slice(0, 500),
        category,
        confidence: 0.8,
        source: "inferred",
      });
    }
  }

  /**
   * Get recent sessions from SessionStore
   */
  async getRecentSessions(limit = 5): Promise<Session[]> {
    if (!this.sessionStore) return [];

    const sessions = await this.sessionStore.listSessions();
    return sessions.slice(0, limit);
  }

  /**
   * Build a context string for system prompt injection
   */
  async buildContextString(): Promise<string> {
    await this.load();

    const sections: string[] = [];

    const activeCommitments = await this.getActiveCommitments();
    if (activeCommitments.length > 0) {
      const commitLines = activeCommitments.map(
        (c) => `- ${c.description} (${c.status})`,
      );
      sections.push(`## Active Commitments\n${commitLines.join("\n")}`);
    }

    if (this.data!.criticalFacts.length > 0) {
      const factLines = this.data!.criticalFacts
        .slice(0, 5)
        .map((f) => `- ${f.fact} [${f.category}]`);
      sections.push(`## Important Facts\n${factLines.join("\n")}`);
    }

    return sections.join("\n\n");
  }

  /**
   * Get statistics
   */
  async getStats(): Promise<{
    totalCommitments: number;
    activeCommitments: number;
    completedCommitments: number;
    criticalFacts: number;
  }> {
    await this.load();

    const commitments = this.data!.commitments;
    return {
      totalCommitments: commitments.length,
      activeCommitments: commitments.filter(
        (c) => c.status === "pending" || c.status === "in_progress",
      ).length,
      completedCommitments: commitments.filter((c) => c.status === "completed")
        .length,
      criticalFacts: this.data!.criticalFacts.length,
    };
  }

  private emptyData(): CrossSessionData {
    return {
      version: 1,
      commitments: [],
      criticalFacts: [],
      loadedAt: new Date().toISOString(),
    };
  }
}
