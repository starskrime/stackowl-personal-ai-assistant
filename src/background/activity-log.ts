/**
 * StackOwl — Background Activity Log
 *
 * Tracks everything the BackgroundOrchestrator does while the user is
 * away. When the user returns, the log is assembled into a human-readable
 * digest so they can see what their assistant was up to.
 *
 * Design principles:
 *   - In-memory only: resets on process restart (background work is ephemeral)
 *   - Append-only: nothing is deleted until explicit clear()
 *   - Bounded: max 50 entries (ring buffer semantics)
 *   - No LLM calls: digest formatting is deterministic
 */

// ─── Types ────────────────────────────────────────────────────────

export type ActivityType =
  | "desire_executed"     // A desire was researched and a pellet was saved
  | "desire_attempted"    // A desire was attempted but didn't produce a pellet
  | "pellet_created"      // A pellet was created (any source)
  | "proactive_ping"      // A proactive message was sent
  | "memory_consolidated" // Memory consolidation ran
  | "pattern_detected"    // A recurring pattern was noticed
  | "loop_detected";      // User loop detected

export interface ActivityEntry {
  type: ActivityType;
  title: string;
  detail?: string;
  timestamp: number; // epoch ms
}

// ─── ActivityLog ──────────────────────────────────────────────────

export class ActivityLog {
  private entries: ActivityEntry[] = [];
  private readonly MAX_ENTRIES = 50;

  add(type: ActivityType, title: string, detail?: string): void {
    this.entries.push({ type, title, detail, timestamp: Date.now() });
    // Ring buffer: drop oldest when full
    if (this.entries.length > this.MAX_ENTRIES) {
      this.entries.shift();
    }
  }

  /**
   * Return entries that happened after `sinceMs` epoch timestamp.
   */
  getSince(sinceMs: number): ActivityEntry[] {
    return this.entries.filter((e) => e.timestamp > sinceMs);
  }

  /** All entries */
  getAll(): ActivityEntry[] {
    return [...this.entries];
  }

  clear(): void {
    this.entries = [];
  }

  /**
   * Build a short human-readable digest of recent activity.
   * Returns null if nothing happened worth mentioning.
   */
  buildDigest(sinceMs: number, owlName: string): string | null {
    const recent = this.getSince(sinceMs);
    if (recent.length === 0) return null;

    const groups = this.groupByType(recent);
    const lines: string[] = [];

    const desiresDone = groups.get("desire_executed") ?? [];
    const pings = groups.get("proactive_ping") ?? [];
    const pellets = groups.get("pellet_created") ?? [];
    const patterns = groups.get("pattern_detected") ?? [];
    const loops = groups.get("loop_detected") ?? [];

    if (desiresDone.length > 0) {
      lines.push(
        `🔍 Researched ${desiresDone.length} topic${desiresDone.length > 1 ? "s" : ""} from my curiosity queue:` +
        `\n${desiresDone.map((e) => `  • ${e.title}`).join("\n")}`,
      );
    }

    if (pellets.length > 0) {
      lines.push(
        `📌 Saved ${pellets.length} knowledge pellet${pellets.length > 1 ? "s" : ""}:` +
        `\n${pellets.map((e) => `  • ${e.title}`).join("\n")}`,
      );
    }

    if (patterns.length > 0) {
      lines.push(
        `💡 Noticed ${patterns.length} pattern${patterns.length > 1 ? "s" : ""}:` +
        `\n${patterns.map((e) => `  • ${e.title}`).join("\n")}`,
      );
    }

    if (loops.length > 0) {
      lines.push(
        `🔄 Spotted a recurring question pattern — I'll flag it when we talk:` +
        `\n${loops.map((e) => `  • ${e.title}`).join("\n")}`,
      );
    }

    if (pings.length > 0) {
      lines.push(`💬 Sent ${pings.length} proactive message${pings.length > 1 ? "s" : ""} while you were away.`);
    }

    if (lines.length === 0) return null;

    const idleMinutes = Math.round((Date.now() - sinceMs) / 60_000);
    const idleStr = idleMinutes < 60
      ? `${idleMinutes}m`
      : `${Math.round(idleMinutes / 60)}h`;

    return `*${owlName} while you were away (${idleStr}):*\n\n` + lines.join("\n\n");
  }

  // ─── Private ─────────────────────────────────────────────────

  private groupByType(entries: ActivityEntry[]): Map<ActivityType, ActivityEntry[]> {
    const map = new Map<ActivityType, ActivityEntry[]>();
    for (const e of entries) {
      const group = map.get(e.type) ?? [];
      group.push(e);
      map.set(e.type, group);
    }
    return map;
  }
}
