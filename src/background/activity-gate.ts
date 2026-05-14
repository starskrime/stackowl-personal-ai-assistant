import { createHash } from "node:crypto";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

export class ActivityGate {
  constructor(private db: MemoryDatabase) {}

  /**
   * Returns true if there is new user interaction since this job last ran.
   * Returns false if nothing has changed — caller should skip the LLM call.
   */
  async hasNewActivity(jobId: string): Promise<boolean> {
    log.engine.debug("activity-gate.hasNewActivity: entry", { jobId });

    const currentHash = this.currentHash();
    const lastSeen = this.db.activityGate.getHash(jobId);

    if (currentHash === null) {
      log.engine.debug("activity-gate.hasNewActivity: no messages exist — skipping", { jobId });
      return false;
    }

    if (lastSeen === null) {
      log.engine.debug("activity-gate.hasNewActivity: first run for job — proceeding", { jobId });
      return true;
    }

    const changed = currentHash !== lastSeen;
    log.engine.debug("activity-gate.hasNewActivity: exit", { jobId, changed, currentHash: currentHash.slice(0, 8), lastSeen: lastSeen.slice(0, 8) });
    return changed;
  }

  /**
   * Record that this job has processed up to the current user message.
   * Call after a successful LLM job run.
   */
  async markSeen(jobId: string): Promise<void> {
    log.engine.debug("activity-gate.markSeen: entry", { jobId });
    const hash = this.currentHash();
    if (hash === null) {
      log.engine.debug("activity-gate.markSeen: no messages — nothing to mark", { jobId });
      return;
    }
    this.db.activityGate.setHash(jobId, hash);
    log.engine.debug("activity-gate.markSeen: exit", { jobId, hash: hash.slice(0, 8) });
  }

  private currentHash(): string | null {
    const row = (this.db as any)["db"].prepare(
      "SELECT id, content FROM messages WHERE role = 'user' ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string; content: string } | undefined;

    if (!row) return null;
    return createHash("sha1").update(row.id + row.content).digest("hex");
  }
}
