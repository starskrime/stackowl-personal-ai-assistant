import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type {
  ChatMessage,
  TimelineSnapshot,
  SessionFork,
  TimelineView,
  ReplayOptions,
  ReplayMessage,
} from "./types.js";

const log = new Logger("TIMELINE");

const AUTO_SNAPSHOT_INTERVAL = 10; // messages between auto-snapshots
const DEFAULT_MAX_PER_SESSION = 20;

interface TimelineData {
  snapshots: TimelineSnapshot[];
  forks: SessionFork[];
}

export class TimelineManager {
  private snapshots = new Map<string, TimelineSnapshot>();
  private forks = new Map<string, SessionFork>();
  private filePath: string;

  constructor(private workspacePath: string) {
    this.filePath = join(workspacePath, "timelines.json");
  }

  async load(): Promise<void> {
    if (!existsSync(this.filePath)) return;

    try {
      const raw = readFileSync(this.filePath, "utf-8");
      const data = JSON.parse(raw) as TimelineData;
      for (const s of data.snapshots) this.snapshots.set(s.id, s);
      for (const f of data.forks) this.forks.set(f.id, f);
      log.info(
        `Loaded ${this.snapshots.size} snapshots, ${this.forks.size} forks`,
      );
    } catch (err) {
      log.warn(`Failed to load timeline data: ${err}`);
    }
  }

  createSnapshot(
    sessionId: string,
    messages: ChatMessage[],
    owlName: string,
    description?: string,
  ): TimelineSnapshot {
    const snapshot: TimelineSnapshot = {
      id: randomUUID(),
      sessionId,
      messageIndex: messages.length,
      messages: [...messages],
      metadata: {
        owlName,
        snapshotAt: new Date().toISOString(),
        description,
      },
    };

    this.snapshots.set(snapshot.id, snapshot);
    log.info(
      `Snapshot created: ${snapshot.id} (session ${sessionId}, ${messages.length} msgs)`,
    );
    return snapshot;
  }

  autoSnapshot(
    sessionId: string,
    messages: ChatMessage[],
    owlName: string,
  ): TimelineSnapshot | null {
    const existing = this.getSnapshots(sessionId);
    if (existing.length > 0) {
      const last = existing[existing.length - 1];
      if (messages.length - last.messageIndex < AUTO_SNAPSHOT_INTERVAL) {
        return null;
      }
    }

    return this.createSnapshot(
      sessionId,
      messages,
      owlName,
      `Auto-snapshot at message ${messages.length}`,
    );
  }

  fork(snapshotId: string, newSessionId: string, reason?: string): SessionFork {
    const snapshot = this.snapshots.get(snapshotId);
    if (!snapshot) {
      throw new Error(`Snapshot ${snapshotId} not found`);
    }

    const fork: SessionFork = {
      id: randomUUID(),
      parentSessionId: snapshot.sessionId,
      parentSnapshotId: snapshotId,
      forkIndex: snapshot.messageIndex,
      newSessionId,
      forkReason: reason,
      createdAt: new Date().toISOString(),
    };

    this.forks.set(fork.id, fork);
    log.info(
      `Session forked: ${snapshot.sessionId} → ${newSessionId} at index ${snapshot.messageIndex}`,
    );
    return fork;
  }

  getTimeline(sessionId: string): TimelineView | null {
    const snapshots = this.getSnapshots(sessionId);
    const forks = this.getForks(sessionId);

    if (snapshots.length === 0 && forks.length === 0) return null;

    const allTimes = [
      ...snapshots.map((s) => s.metadata.snapshotAt),
      ...forks.map((f) => f.createdAt),
    ];

    return {
      sessionId,
      snapshots,
      forks,
      totalMessages:
        snapshots.length > 0 ? snapshots[snapshots.length - 1].messageIndex : 0,
      created:
        allTimes.length > 0 ? allTimes.sort()[0] : new Date().toISOString(),
      lastActivity:
        allTimes.length > 0 ? allTimes.sort().pop()! : new Date().toISOString(),
    };
  }

  getSnapshots(sessionId: string): TimelineSnapshot[] {
    const result: TimelineSnapshot[] = [];
    for (const [, s] of this.snapshots) {
      if (s.sessionId === sessionId) result.push(s);
    }
    return result.sort((a, b) => a.messageIndex - b.messageIndex);
  }

  getMessagesAt(snapshotId: string): ChatMessage[] {
    const snapshot = this.snapshots.get(snapshotId);
    return snapshot ? [...snapshot.messages] : [];
  }

  getForks(sessionId: string): SessionFork[] {
    const result: SessionFork[] = [];
    for (const [, f] of this.forks) {
      if (f.parentSessionId === sessionId) result.push(f);
    }
    return result.sort((a, b) => a.forkIndex - b.forkIndex);
  }

  replay(
    sessionId: string,
    options: ReplayOptions = { speed: "instant" },
  ): ReplayMessage[] {
    const snapshots = this.getSnapshots(sessionId);
    if (snapshots.length === 0) return [];

    const latest = snapshots[snapshots.length - 1];
    const messages = latest.messages;
    const from = options.fromIndex ?? 0;
    const to = options.toIndex ?? messages.length;
    const filter = options.filter ?? "all";

    const forkIndices = new Set(
      this.getForks(sessionId).map((f) => f.forkIndex),
    );

    const result: ReplayMessage[] = [];
    for (let i = from; i < to && i < messages.length; i++) {
      const msg = messages[i];
      if (filter === "user_only" && msg.role !== "user") continue;
      if (filter === "assistant_only" && msg.role !== "assistant") continue;

      result.push({
        index: i,
        role: msg.role,
        content: msg.content,
        isForked: forkIndices.has(i),
      });
    }

    return result;
  }

  compare(
    snapshotIdA: string,
    snapshotIdB: string,
  ): {
    divergenceIndex: number;
    messagesOnlyInA: ChatMessage[];
    messagesOnlyInB: ChatMessage[];
    commonMessages: number;
  } {
    const msgsA = this.getMessagesAt(snapshotIdA);
    const msgsB = this.getMessagesAt(snapshotIdB);

    let divergence = 0;
    const minLen = Math.min(msgsA.length, msgsB.length);

    while (divergence < minLen) {
      if (
        msgsA[divergence].role !== msgsB[divergence].role ||
        msgsA[divergence].content !== msgsB[divergence].content
      ) {
        break;
      }
      divergence++;
    }

    return {
      divergenceIndex: divergence,
      messagesOnlyInA: msgsA.slice(divergence),
      messagesOnlyInB: msgsB.slice(divergence),
      commonMessages: divergence,
    };
  }

  prune(maxPerSession = DEFAULT_MAX_PER_SESSION): number {
    const bySession = new Map<string, TimelineSnapshot[]>();
    for (const [, s] of this.snapshots) {
      const list = bySession.get(s.sessionId) ?? [];
      list.push(s);
      bySession.set(s.sessionId, list);
    }

    let deleted = 0;
    for (const [, list] of bySession) {
      if (list.length <= maxPerSession) continue;
      list.sort((a, b) => b.messageIndex - a.messageIndex);
      const toRemove = list.slice(maxPerSession);
      for (const s of toRemove) {
        this.snapshots.delete(s.id);
        deleted++;
      }
    }

    if (deleted > 0) log.info(`Pruned ${deleted} old snapshots`);
    return deleted;
  }

  async save(): Promise<void> {
    try {
      mkdirSync(this.workspacePath, { recursive: true });
      const data: TimelineData = {
        snapshots: Array.from(this.snapshots.values()),
        forks: Array.from(this.forks.values()),
      };
      writeFileSync(this.filePath, JSON.stringify(data, null, 2), "utf-8");
    } catch (err) {
      log.error(`Failed to save timeline data: ${err}`);
    }
  }
}
