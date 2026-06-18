/**
 * StackOwl — Response Feedback Store
 *
 * Persists 👍/👎 signals from users.
 * Records are used to:
 *   - Boost confidence of liked success recipes in FactStore
 *   - Trigger re-synthesis for disliked responses via CognitiveLoop
 *   - Provide a queryable quality history for analytics
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";
import type { MemoryDatabase } from "../memory/db.js";

export type FeedbackSignal = "like" | "dislike";

export interface FeedbackRecord {
  id: string;
  sessionId: string;
  userId: string;
  signal: FeedbackSignal;
  userMessage: string;
  assistantSummary: string;
  toolsUsed: string[];
  timestamp: string;
}

const MAX_RECORDS = 1_000;

export class FeedbackStore {
  private records: FeedbackRecord[] = [];
  private filePath: string;
  private loaded = false;
  private db?: MemoryDatabase;
  private owlName: string;

  constructor(workspacePath: string, db?: MemoryDatabase, owlName = "default") {
    this.filePath = join(workspacePath, "memory", "feedback.json");
    this.db = db;
    this.owlName = owlName;
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    if (!this.db) {
      try {
        if (existsSync(this.filePath)) {
          const raw = await readFile(this.filePath, "utf-8");
          this.records = JSON.parse(raw) as FeedbackRecord[];
        }
      } catch {
        // Non-fatal — start fresh
      }
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    if (this.db) return; // DB writes happen in record()
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(
      this.filePath,
      JSON.stringify(this.records, null, 2),
      "utf-8",
    );
  }

  async record(entry: FeedbackRecord): Promise<void> {
    if (this.db) {
      this.db.feedback.record({
        sessionId: entry.sessionId,
        userId: entry.userId,
        owlName: this.owlName,
        signal: entry.signal,
        userMessage: entry.userMessage,
        assistantSummary: entry.assistantSummary,
        toolsUsed: entry.toolsUsed,
      });
      log.engine.info(
        `[FeedbackStore] ${entry.signal === "like" ? "👍" : "👎"} recorded for session ${entry.sessionId} (SQLite)`,
      );
      return;
    }

    await this.load();
    this.records.push(entry);
    if (this.records.length > MAX_RECORDS) {
      this.records = this.records.slice(-MAX_RECORDS);
    }
    await this.save();
    log.engine.info(
      `[FeedbackStore] ${entry.signal === "like" ? "👍" : "👎"} recorded for session ${entry.sessionId}`,
    );
  }

  getRecent(limit = 50): FeedbackRecord[] {
    if (this.db) {
      const dbRecords = this.db.feedback.getRecent(limit);
      return dbRecords.map((r) => ({
        id: r.id,
        sessionId: r.sessionId,
        userId: r.userId,
        signal: r.signal,
        userMessage: r.userMessage ?? "",
        assistantSummary: r.assistantSummary ?? "",
        toolsUsed: r.toolsUsed,
        timestamp: r.createdAt,
      }));
    }
    return this.records.slice(-limit);
  }

  /** 0–1 ratio of liked responses. 0.5 if no data. */
  getLikeRatio(): number {
    if (this.db) {
      return this.db.feedback.getRatioForOwl(this.owlName);
    }
    if (this.records.length === 0) return 0.5;
    return this.records.filter((r) => r.signal === "like").length / this.records.length;
  }
}
