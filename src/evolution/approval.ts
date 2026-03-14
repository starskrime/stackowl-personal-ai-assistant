/**
 * StackOwl — Skill Synthesis Approval System
 *
 * Before auto-synthesizing a new skill/tool, present the proposal
 * to the user for approval. Supports approve/reject/defer decisions.
 * Deferred proposals are queued on disk for later review.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { PendingCapabilityGap } from "../engine/runtime.js";
import { log } from "../logger.js";

export interface ApprovalRequest {
  id: string;
  type: "skill_synthesis";
  skillName: string;
  description: string;
  rationale: string;
  gap: PendingCapabilityGap;
  timestamp: string;
}

export type ApprovalDecision = "approved" | "rejected" | "deferred";

export type ApprovalCallback = (
  request: ApprovalRequest,
) => Promise<ApprovalDecision>;

// ─── Approval Queue (file-backed) ────────────────────────────

export class ApprovalQueue {
  private queuePath: string;

  constructor(workspacePath: string) {
    this.queuePath = join(workspacePath, "pending_approvals.json");
  }

  async enqueue(request: ApprovalRequest): Promise<void> {
    const queue = await this.load();
    queue.push(request);
    await this.save(queue);
    log.evolution.info(
      `[approval] Deferred: "${request.skillName}" queued for later review`,
    );
  }

  async dequeue(id: string): Promise<ApprovalRequest | undefined> {
    const queue = await this.load();
    const idx = queue.findIndex((r) => r.id === id);
    if (idx === -1) return undefined;
    const [removed] = queue.splice(idx, 1);
    await this.save(queue);
    return removed;
  }

  async list(): Promise<ApprovalRequest[]> {
    return this.load();
  }

  async clear(): Promise<void> {
    await this.save([]);
  }

  private async load(): Promise<ApprovalRequest[]> {
    if (!existsSync(this.queuePath)) return [];
    try {
      return JSON.parse(await readFile(this.queuePath, "utf-8"));
    } catch {
      return [];
    }
  }

  private async save(queue: ApprovalRequest[]): Promise<void> {
    const dir = join(this.queuePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(this.queuePath, JSON.stringify(queue, null, 2), "utf-8");
  }
}
