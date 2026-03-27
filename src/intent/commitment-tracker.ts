/**
 * StackOwl — Commitment Tracker
 *
 * Monitors owl commitments across all intents and fires follow-up
 * messages when deadlines are reached. Works with IntentStateMachine
 * to extract commitments and with the ProactivePinger to deliver them.
 *
 * Priority: commitments always beat generic check-ins.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export interface TrackedCommitment {
  id: string;
  intentId: string;
  sessionId: string;
  statement: string;
  deadline: number;
  followUpMessage: string;
  context: string;
  status: "pending" | "sent" | "acknowledged" | "dismissed" | "expired";
  createdAt: number;
  sentAt?: number;
  acknowledgedAt?: number;
  dismissedAt?: number;
  expiresAt?: number;
}

export interface CommitmentTracker {
  track(
    c: Omit<TrackedCommitment, "id" | "status" | "createdAt">,
  ): TrackedCommitment;
  getDue(): TrackedCommitment[];
  getPending(): TrackedCommitment[];
  markSent(id: string): void;
  markAcknowledged(id: string): void;
  markDismissed(id: string): void;
  markExpired(id: string): void;
  toContextString(): string;
}

export class CommitmentTrackerImpl implements CommitmentTracker {
  private commitments: Map<string, TrackedCommitment> = new Map();
  private filePath: string;
  private loaded = false;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "intents", "commitments.json");
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data) as TrackedCommitment[];
        for (const c of parsed) {
          if (c.status !== "expired" && c.status !== "dismissed") {
            this.commitments.set(c.id, c);
          }
        }
        log.engine.info(
          `[CommitmentTracker] Loaded ${this.commitments.size} active commitments`,
        );
      }
    } catch (err) {
      log.engine.warn(
        `[CommitmentTracker] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    try {
      const dir = join(this.filePath, "..");
      if (!existsSync(dir)) await mkdir(dir, { recursive: true });
      await writeFile(
        this.filePath,
        JSON.stringify([...this.commitments.values()], null, 2),
        "utf-8",
      );
    } catch (err) {
      log.engine.error(
        `[CommitmentTracker] Save failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  track(
    c: Omit<TrackedCommitment, "id" | "status" | "createdAt">,
  ): TrackedCommitment {
    const tracked: TrackedCommitment = {
      ...c,
      id: `tracked_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      status: "pending",
      createdAt: Date.now(),
    };
    this.commitments.set(tracked.id, tracked);
    log.engine.info(
      `[CommitmentTracker] Tracked: "${c.statement.slice(0, 50)}" due ${new Date(c.deadline).toLocaleString()}`,
    );
    this.save().catch(() => {});
    return tracked;
  }

  getDue(): TrackedCommitment[] {
    const now = Date.now();
    return [...this.commitments.values()].filter(
      (c) =>
        c.status === "pending" &&
        c.deadline <= now &&
        (!c.expiresAt || c.expiresAt > now),
    );
  }

  getPending(): TrackedCommitment[] {
    return [...this.commitments.values()].filter((c) => c.status === "pending");
  }

  markSent(id: string): void {
    const c = this.commitments.get(id);
    if (!c || c.status !== "pending") return;
    c.status = "sent";
    c.sentAt = Date.now();
    log.engine.info(
      `[CommitmentTracker] Sent follow-up for: "${c.statement.slice(0, 50)}"`,
    );
    this.save().catch(() => {});
  }

  markAcknowledged(id: string): void {
    const c = this.commitments.get(id);
    if (!c) return;
    c.status = "acknowledged";
    c.acknowledgedAt = Date.now();
    log.engine.info(
      `[CommitmentTracker] Commitment acknowledged: "${c.statement.slice(0, 50)}"`,
    );
    this.save().catch(() => {});
  }

  markDismissed(id: string): void {
    const c = this.commitments.get(id);
    if (!c) return;
    c.status = "dismissed";
    c.dismissedAt = Date.now();
    log.engine.info(
      `[CommitmentTracker] Commitment dismissed: "${c.statement.slice(0, 50)}"`,
    );
    this.save().catch(() => {});
  }

  markExpired(id: string): void {
    const c = this.commitments.get(id);
    if (!c) return;
    c.status = "expired";
    log.engine.info(
      `[CommitmentTracker] Commitment expired: "${c.statement.slice(0, 50)}"`,
    );
    this.save().catch(() => {});
  }

  toContextString(): string {
    const pending = this.getPending();
    if (pending.length === 0) return "";

    const lines = ["<pending_commitments>"];
    for (const c of pending.slice(0, 5)) {
      const due = c.deadline <= Date.now();
      const icon = due ? "🔔" : "⏳";
      const timeStr = due
        ? "DUE NOW"
        : `due ${new Date(c.deadline).toLocaleDateString()} ${new Date(c.deadline).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
      lines.push(`  ${icon} [${timeStr}] ${c.statement.slice(0, 60)}`);
    }
    lines.push("</pending_commitments>");
    return lines.join("\n");
  }
}
