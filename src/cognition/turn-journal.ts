/**
 * TurnJournal — append-only WAL for cognitive turn durability.
 *
 * Guards against the "crash gap": process dies after Execute delivers a
 * response but before Consolidate writes preferences/pellets to SQLite +
 * LanceDB. On next startup, incomplete entries are replayed through
 * Consolidate so no extracted knowledge is permanently lost.
 *
 * Storage: {dataDir}/turn-journal.jsonl (JSONL, one record per line)
 *
 * Record types:
 *   { _type: "append", ...TurnJournalEntry }  — written at enqueue time
 *   { _type: "commit", id, completedAt }       — written after Consolidate completes
 *
 * Reading incomplete entries: scan all lines, track appends, remove
 * committed ids. Any remaining append records are incomplete.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import type { ExecutionPlan } from "./dispatch.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface TurnJournalEntry {
  id: string;
  sessionId: string;
  turnIndex: number;
  userId?: string;
  channelId?: string;
  /** Truncated to 500 chars */
  userMessage: string;
  /** Truncated to 1500 chars — present when crash-gap replay is possible */
  assistantResponse?: string;
  toolsUsed: string[];
  executionPlan: ExecutionPlan;
  enqueuedAt: string;
}

type AppendRecord = { _type: "append" } & TurnJournalEntry;
type CommitRecord = { _type: "commit"; id: string; completedAt: string };
type JournalRecord = AppendRecord | CommitRecord;

// ─── TurnJournal ──────────────────────────────────────────────────

export class TurnJournal {
  private readonly path: string;
  private disabled = false;

  constructor(dataDir: string) {
    this.path = join(dataDir, "turn-journal.jsonl");
    try {
      if (!existsSync(dataDir)) mkdirSync(dataDir, { recursive: true });
    } catch (err) {
      log.cognition.error("turn-journal.init.failed — journal disabled", err as Error, { dataDir });
      this.disabled = true;
    }
  }

  /**
   * Append a new entry for this turn. Returns the entry id (needed for commit).
   * Synchronous write so the record lands before Execute side-effects fire.
   */
  append(
    turn: {
      sessionId: string;
      turnIndex: number;
      userId?: string;
      channelId?: string;
      userMessage: string;
      assistantResponse?: string;
      toolsUsed: string[];
      executionPlan: ExecutionPlan;
    },
  ): string {
    if (this.disabled) return randomUUID();

    const id = randomUUID();
    const entry: TurnJournalEntry = {
      id,
      sessionId: turn.sessionId,
      turnIndex: turn.turnIndex,
      userId: turn.userId,
      channelId: turn.channelId,
      userMessage: turn.userMessage.slice(0, 500),
      assistantResponse: turn.assistantResponse?.slice(0, 1500),
      toolsUsed: turn.toolsUsed,
      executionPlan: turn.executionPlan,
      enqueuedAt: new Date().toISOString(),
    };

    const record: AppendRecord = { _type: "append", ...entry };
    this.writeLine(record, "append");

    log.cognition.debug("turn-journal.append", {
      id,
      sessionId: turn.sessionId,
      turnIndex: turn.turnIndex,
    });

    return id;
  }

  /**
   * Mark a previously appended entry as complete. Written after Consolidate
   * succeeds — once committed, the entry is excluded from replay.
   */
  commit(id: string): void {
    if (this.disabled) return;

    const record: CommitRecord = {
      _type: "commit",
      id,
      completedAt: new Date().toISOString(),
    };
    this.writeLine(record, "commit");

    log.cognition.debug("turn-journal.commit", { id });
  }

  /**
   * Returns entries that were appended but never committed.
   * Only entries with an assistantResponse can be replayed through Consolidate.
   */
  getIncomplete(): TurnJournalEntry[] {
    if (this.disabled || !existsSync(this.path)) return [];

    try {
      const lines = readFileSync(this.path, "utf8").split("\n").filter(Boolean);
      const appends = new Map<string, TurnJournalEntry>();
      const committed = new Set<string>();

      for (const line of lines) {
        try {
          const record = JSON.parse(line) as JournalRecord;
          if (record._type === "append") {
            const { _type: _, ...entry } = record;
            appends.set(entry.id, entry as TurnJournalEntry);
          } else if (record._type === "commit") {
            committed.add(record.id);
          }
        } catch { /* skip malformed lines */ }
      }

      const incomplete: TurnJournalEntry[] = [];
      for (const [id, entry] of appends) {
        if (!committed.has(id)) incomplete.push(entry);
      }

      log.cognition.info("turn-journal.getIncomplete", {
        totalLines: lines.length,
        appends: appends.size,
        committed: committed.size,
        incomplete: incomplete.length,
      });

      return incomplete;
    } catch (err) {
      log.cognition.error("turn-journal.read.failed", err as Error, { path: this.path });
      return [];
    }
  }

  /**
   * Remove completed entries older than maxAgeDays. Rewrites the file with
   * only recent uncommitted entries + recent committed entries.
   * Call once at startup after replaying incomplete entries.
   */
  prune(maxAgeDays = 7): void {
    if (this.disabled || !existsSync(this.path)) return;

    const cutoff = Date.now() - maxAgeDays * 86_400_000;

    try {
      const lines = readFileSync(this.path, "utf8").split("\n").filter(Boolean);
      const appends = new Map<string, AppendRecord>();
      const commits = new Map<string, CommitRecord>();

      for (const line of lines) {
        try {
          const record = JSON.parse(line) as JournalRecord;
          if (record._type === "append") appends.set(record.id, record);
          else if (record._type === "commit") commits.set(record.id, record);
        } catch { /* skip */ }
      }

      const kept: JournalRecord[] = [];
      for (const [id, append] of appends) {
        const commit = commits.get(id);
        const isOld = commit && new Date(commit.completedAt).getTime() < cutoff;
        if (!isOld) {
          kept.push(append);
          if (commit) kept.push(commit);
        }
      }

      writeFileSync(this.path, kept.map((r) => JSON.stringify(r)).join("\n") + (kept.length ? "\n" : ""), "utf8");
      log.cognition.info("turn-journal.pruned", {
        before: lines.length,
        after: kept.length,
        maxAgeDays,
      });
    } catch (err) {
      log.cognition.error("turn-journal.prune.failed", err as Error, { path: this.path });
    }
  }

  // ─── Internal ─────────────────────────────────────────────────

  private writeLine(record: JournalRecord, op: string): void {
    try {
      appendFileSync(this.path, JSON.stringify(record) + "\n", "utf8");
    } catch (err) {
      log.cognition.error(`turn-journal.write.failed (${op})`, err as Error, { path: this.path });
      this.disabled = true; // disable to avoid cascading I/O errors
    }
  }
}
