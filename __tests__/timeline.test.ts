import { describe, it, expect, beforeEach, vi } from "vitest";
import { TimelineManager } from "../src/timeline/index.js";
import type {
  ChatMessage,
  TimelineSnapshot,
  SessionFork,
  ReplayOptions,
} from "../src/timeline/types.js";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

let uuidCounter = 0;
vi.mock("node:crypto", () => ({
  randomUUID: vi.fn(() => `test-uuid-${++uuidCounter}`),
}));

vi.mock("node:fs", () => ({
  existsSync: vi.fn(),
  readFileSync: vi.fn(),
  writeFileSync: vi.fn(),
  mkdirSync: vi.fn(),
}));

vi.mock("node:path", () => ({
  join: vi.fn((...args: string[]) => args.join("/")),
}));

vi.mock("../src/logger.js", () => ({
  Logger: vi.fn().mockImplementation(() => ({
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  })),
}));

describe("TimelineManager", () => {
  let manager: TimelineManager;
  const workspacePath = "/test/workspace";

  const createTestMessages = (count: number): ChatMessage[] => {
    return Array.from({ length: count }, (_, i) => ({
      role: i % 2 === 0 ? "user" : "assistant",
      content: `Message ${i}`,
    }));
  };

  beforeEach(() => {
    vi.clearAllMocks();
    uuidCounter = 0;
    manager = new TimelineManager(workspacePath);
  });

  describe("constructor", () => {
    it("should set workspace path and file path", () => {
      expect(manager).toBeDefined();
      expect(join).toHaveBeenCalledWith(workspacePath, "timelines.json");
    });
  });

  describe("load", () => {
    it("should return early if file does not exist", async () => {
      vi.mocked(existsSync).mockReturnValue(false);

      await manager.load();

      expect(existsSync).toHaveBeenCalled();
      expect(readFileSync).not.toHaveBeenCalled();
    });

    it("should load snapshots and forks from file", async () => {
      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFileSync).mockReturnValue(
        JSON.stringify({
          snapshots: [
            {
              id: "snap-1",
              sessionId: "session-1",
              messageIndex: 5,
              messages: [{ role: "user", content: "test" }],
              metadata: {
                owlName: "TestOwl",
                snapshotAt: "2024-01-01T00:00:00Z",
              },
            },
          ],
          forks: [
            {
              id: "fork-1",
              parentSessionId: "session-1",
              parentSnapshotId: "snap-1",
              forkIndex: 3,
              newSessionId: "session-2",
              createdAt: "2024-01-01T00:00:00Z",
            },
          ],
        }),
      );

      await manager.load();

      expect(readFileSync).toHaveBeenCalled();
    });

    it("should handle parse errors gracefully", async () => {
      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFileSync).mockReturnValue("invalid json");

      await manager.load();

      expect(readFileSync).toHaveBeenCalled();
    });
  });

  describe("createSnapshot", () => {
    it("should create a snapshot with correct properties", () => {
      const messages = createTestMessages(3);
      const snapshot = manager.createSnapshot(
        "session-1",
        messages,
        "Noctua",
        "Test snapshot",
      );

      expect(snapshot.id).toBe("test-uuid-1");
      expect(snapshot.sessionId).toBe("session-1");
      expect(snapshot.messageIndex).toBe(3);
      expect(snapshot.messages).toEqual(messages);
      expect(snapshot.metadata.owlName).toBe("Noctua");
      expect(snapshot.metadata.description).toBe("Test snapshot");
      expect(snapshot.metadata.snapshotAt).toBeDefined();
    });

    it("should store snapshot internally", () => {
      const messages = createTestMessages(2);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");

      const retrieved = manager.getTimeline("session-1");
      expect(retrieved).not.toBeNull();
      expect(retrieved!.snapshots).toContainEqual(snapshot);
    });

    it("should allow creating multiple snapshots for same session", () => {
      const msgs1 = createTestMessages(5);
      const msgs2 = createTestMessages(10);

      manager.createSnapshot("session-1", msgs1, "Owl", "First");
      manager.createSnapshot("session-1", msgs2, "Owl", "Second");

      const snapshots = manager.getSnapshots("session-1");
      expect(snapshots).toHaveLength(2);
      expect(snapshots[0].messageIndex).toBe(5);
      expect(snapshots[1].messageIndex).toBe(10);
    });
  });

  describe("autoSnapshot", () => {
    it("should create snapshot when no existing snapshots exist", () => {
      const messages = createTestMessages(5);
      const result = manager.autoSnapshot("session-1", messages, "Owl");

      expect(result).not.toBeNull();
      expect(result!.messageIndex).toBe(5);
    });

    it("should create snapshot when interval is met", () => {
      const messages1 = createTestMessages(10);
      manager.createSnapshot("session-1", messages1, "Owl");

      const messages2 = createTestMessages(25);
      const result = manager.autoSnapshot("session-1", messages2, "Owl");

      expect(result).not.toBeNull();
      expect(result!.messageIndex).toBe(25);
      expect(result!.metadata.description).toContain("Auto-snapshot");
    });

    it("should return null if messages since last snapshot is less than interval", () => {
      const messages1 = createTestMessages(10);
      manager.createSnapshot("session-1", messages1, "Owl");

      const messages2 = createTestMessages(15);
      const result = manager.autoSnapshot("session-1", messages2, "Owl");

      expect(result).toBeNull();
    });
  });

  describe("fork", () => {
    it("should create a fork from a snapshot", () => {
      const messages = createTestMessages(10);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");

      const fork = manager.fork(snapshot.id, "session-2", "Testing fork");

      expect(fork.id).toBe("test-uuid-2");
      expect(fork.parentSessionId).toBe("session-1");
      expect(fork.parentSnapshotId).toBe(snapshot.id);
      expect(fork.forkIndex).toBe(10);
      expect(fork.newSessionId).toBe("session-2");
      expect(fork.forkReason).toBe("Testing fork");
      expect(fork.createdAt).toBeDefined();
    });

    it("should throw error if snapshot not found", () => {
      expect(() => manager.fork("non-existent", "session-2")).toThrow(
        "Snapshot non-existent not found",
      );
    });

    it("should store fork internally", () => {
      const messages = createTestMessages(5);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");
      manager.fork(snapshot.id, "session-2", "Test");

      const forks = manager.getForks("session-1");
      expect(forks).toHaveLength(1);
      expect(forks[0].newSessionId).toBe("session-2");
    });
  });

  describe("getTimeline", () => {
    it("should return null if no snapshots or forks exist", () => {
      const result = manager.getTimeline("non-existent");
      expect(result).toBeNull();
    });

    it("should return timeline with snapshots and forks", () => {
      const messages = createTestMessages(10);
      manager.createSnapshot("session-1", messages, "Owl", "Test");

      const timeline = manager.getTimeline("session-1");

      expect(timeline).not.toBeNull();
      expect(timeline!.sessionId).toBe("session-1");
      expect(timeline!.snapshots).toHaveLength(1);
      expect(timeline!.totalMessages).toBe(10);
    });

    it("should calculate created and lastActivity timestamps", () => {
      const messages = createTestMessages(5);
      manager.createSnapshot("session-1", messages, "Owl");

      const timeline = manager.getTimeline("session-1");

      expect(timeline!.created).toBeDefined();
      expect(timeline!.lastActivity).toBeDefined();
    });
  });

  describe("getSnapshots", () => {
    it("should return empty array for non-existent session", () => {
      const result = manager.getSnapshots("non-existent");
      expect(result).toHaveLength(0);
    });

    it("should return snapshots sorted by messageIndex", () => {
      manager.createSnapshot("session-1", createTestMessages(5), "Owl");
      manager.createSnapshot("session-1", createTestMessages(15), "Owl");
      manager.createSnapshot("session-1", createTestMessages(10), "Owl");

      const snapshots = manager.getSnapshots("session-1");

      expect(snapshots).toHaveLength(3);
      expect(snapshots[0].messageIndex).toBe(5);
      expect(snapshots[1].messageIndex).toBe(10);
      expect(snapshots[2].messageIndex).toBe(15);
    });

    it("should only return snapshots for specified session", () => {
      manager.createSnapshot("session-1", createTestMessages(5), "Owl");
      manager.createSnapshot("session-2", createTestMessages(10), "Owl");

      const session1Snapshots = manager.getSnapshots("session-1");
      const session2Snapshots = manager.getSnapshots("session-2");

      expect(session1Snapshots).toHaveLength(1);
      expect(session2Snapshots).toHaveLength(1);
    });
  });

  describe("getMessagesAt", () => {
    it("should return empty array for non-existent snapshot", () => {
      const result = manager.getMessagesAt("non-existent");
      expect(result).toHaveLength(0);
    });

    it("should return messages from snapshot", () => {
      const messages = createTestMessages(5);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");

      const retrieved = manager.getMessagesAt(snapshot.id);

      expect(retrieved).toEqual(messages);
    });

    it("should return a copy of messages array", () => {
      const messages = createTestMessages(3);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");

      const retrieved = manager.getMessagesAt(snapshot.id);
      retrieved.push({ role: "user", content: "modified" });

      const second = manager.getMessagesAt(snapshot.id);
      expect(second).toHaveLength(3);
    });
  });

  describe("getForks", () => {
    it("should return empty array for non-existent session", () => {
      const result = manager.getForks("non-existent");
      expect(result).toHaveLength(0);
    });

    it("should return forks sorted by forkIndex", () => {
      const messages = createTestMessages(20);
      const snap1 = manager.createSnapshot(
        "session-1",
        messages.slice(0, 5),
        "Owl",
      );
      const snap2 = manager.createSnapshot(
        "session-1",
        messages.slice(0, 15),
        "Owl",
      );

      manager.fork(snap1.id, "session-2");
      manager.fork(snap2.id, "session-3");

      const forks = manager.getForks("session-1");

      expect(forks).toHaveLength(2);
      expect(forks[0].forkIndex).toBe(5);
      expect(forks[1].forkIndex).toBe(15);
    });
  });

  describe("replay", () => {
    it("should return empty array for non-existent session", () => {
      const result = manager.replay("non-existent");
      expect(result).toHaveLength(0);
    });

    it("should replay all messages by default", () => {
      const messages = createTestMessages(5);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1");

      expect(replayed).toHaveLength(5);
      expect(replayed[0].index).toBe(0);
      expect(replayed[0].content).toBe("Message 0");
    });

    it("should respect fromIndex option", () => {
      const messages = createTestMessages(10);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1", {
        speed: "instant",
        fromIndex: 5,
      });

      expect(replayed).toHaveLength(5);
      expect(replayed[0].index).toBe(5);
    });

    it("should respect toIndex option", () => {
      const messages = createTestMessages(10);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1", {
        speed: "instant",
        toIndex: 5,
      });

      expect(replayed).toHaveLength(5);
      expect(replayed[4].index).toBe(4);
    });

    it("should filter user_only messages", () => {
      const messages = createTestMessages(4);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1", {
        speed: "instant",
        filter: "user_only",
      });

      expect(replayed).toHaveLength(2);
      expect(replayed.every((m) => m.role === "user")).toBe(true);
    });

    it("should filter assistant_only messages", () => {
      const messages = createTestMessages(4);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1", {
        speed: "instant",
        filter: "assistant_only",
      });

      expect(replayed).toHaveLength(2);
      expect(replayed.every((m) => m.role === "assistant")).toBe(true);
    });

    it("should mark forked messages", () => {
      const snapshot = manager.createSnapshot(
        "session-1",
        createTestMessages(5),
        "Owl",
      );
      manager.fork(snapshot.id, "session-2", undefined);

      const messages = createTestMessages(10);
      manager.createSnapshot("session-1", messages, "Owl");

      const replayed = manager.replay("session-1");

      expect(replayed.find((m) => m.index === 5)?.isForked).toBe(true);
    });
  });

  describe("compare", () => {
    it("should return empty comparison for identical snapshots", () => {
      const messages = createTestMessages(5);
      const snap1 = manager.createSnapshot("session-1", messages, "Owl");
      const snap2 = manager.createSnapshot("session-1", messages, "Owl");

      const result = manager.compare(snap1.id, snap2.id);

      expect(result.divergenceIndex).toBe(5);
      expect(result.messagesOnlyInA).toHaveLength(0);
      expect(result.messagesOnlyInB).toHaveLength(0);
      expect(result.commonMessages).toBe(5);
    });

    it("should detect divergence point", () => {
      const messages1 = createTestMessages(5);
      const snap1 = manager.createSnapshot("session-1", messages1, "Owl");

      const messages2 = [
        ...messages1.slice(0, 3),
        { role: "user" as const, content: "Different message" },
        { role: "assistant" as const, content: "Another" },
      ];
      const snap2 = manager.createSnapshot("session-1", messages2, "Owl");

      const result = manager.compare(snap1.id, snap2.id);

      expect(result.divergenceIndex).toBe(3);
      expect(result.commonMessages).toBe(3);
    });

    it("should handle different message roles at divergence", () => {
      const messages1 = createTestMessages(3);
      const snap1 = manager.createSnapshot("session-1", messages1, "Owl");

      const messages2 = [
        ...messages1.slice(0, 2),
        { role: "assistant" as const, content: "Message 2" },
      ];
      const snap2 = manager.createSnapshot("session-1", messages2, "Owl");

      const result = manager.compare(snap1.id, snap2.id);

      expect(result.divergenceIndex).toBe(2);
    });

    it("should handle snapshots of different lengths", () => {
      const messages1 = createTestMessages(5);
      const snap1 = manager.createSnapshot("session-1", messages1, "Owl");

      const messages2 = createTestMessages(3);
      const snap2 = manager.createSnapshot("session-1", messages2, "Owl");

      const result = manager.compare(snap1.id, snap2.id);

      expect(result.divergenceIndex).toBe(3);
      expect(result.messagesOnlyInA).toHaveLength(2);
      expect(result.messagesOnlyInB).toHaveLength(0);
      expect(result.commonMessages).toBe(3);
    });

    it("should return zeros for empty snapshots", () => {
      const messages1: ChatMessage[] = [];
      const messages2: ChatMessage[] = [];
      const snap1 = manager.createSnapshot("session-1", messages1, "Owl");
      const snap2 = manager.createSnapshot("session-1", messages2, "Owl");

      const result = manager.compare(snap1.id, snap2.id);

      expect(result.divergenceIndex).toBe(0);
      expect(result.commonMessages).toBe(0);
    });
  });

  describe("prune", () => {
    it("should return 0 when no snapshots exceed limit", () => {
      manager.createSnapshot("session-1", createTestMessages(5), "Owl");

      const deleted = manager.prune(20);

      expect(deleted).toBe(0);
    });

    it("should delete oldest snapshots beyond limit", () => {
      for (let i = 0; i < 25; i++) {
        manager.createSnapshot("session-1", createTestMessages(i + 1), "Owl");
      }

      const deleted = manager.prune(20);

      expect(deleted).toBe(5);
      expect(manager.getSnapshots("session-1")).toHaveLength(20);
    });

    it("should prune each session independently", () => {
      for (let i = 0; i < 25; i++) {
        manager.createSnapshot("session-1", createTestMessages(i + 1), "Owl");
        manager.createSnapshot("session-2", createTestMessages(i + 1), "Owl");
      }

      const deleted = manager.prune(20);

      expect(deleted).toBe(10);
      expect(manager.getSnapshots("session-1")).toHaveLength(20);
      expect(manager.getSnapshots("session-2")).toHaveLength(20);
    });

    it("should keep newest snapshots when pruning", () => {
      for (let i = 0; i < 25; i++) {
        manager.createSnapshot("session-1", createTestMessages(i + 1), "Owl");
      }

      const deleted = manager.prune(20);
      const snapshots = manager.getSnapshots("session-1");

      expect(snapshots[0].messageIndex).toBe(6);
      expect(snapshots[snapshots.length - 1].messageIndex).toBe(25);
    });
  });

  describe("save", () => {
    it("should write timeline data to file", async () => {
      manager.createSnapshot("session-1", createTestMessages(5), "Owl");

      await manager.save();

      expect(mkdirSync).toHaveBeenCalledWith(workspacePath, {
        recursive: true,
      });
      expect(writeFileSync).toHaveBeenCalled();
    });

    it("should serialize snapshots and forks correctly", async () => {
      const messages = createTestMessages(5);
      const snapshot = manager.createSnapshot("session-1", messages, "Owl");
      manager.fork(snapshot.id, "session-2", "Test");

      await manager.save();

      const writeCall = vi.mocked(writeFileSync).mock.calls[0];
      const writtenData = JSON.parse(writeCall[1] as string);

      expect(writtenData.snapshots).toHaveLength(1);
      expect(writtenData.forks).toHaveLength(1);
    });

    it("should handle write errors gracefully", async () => {
      manager.createSnapshot("session-1", createTestMessages(5), "Owl");
      vi.mocked(writeFileSync).mockImplementation(() => {
        throw new Error("Write failed");
      });

      await expect(manager.save()).resolves.not.toThrow();
    });
  });

  describe("load and save integration", () => {
    it("should persist and restore snapshots across load/save cycles", async () => {
      const messages = createTestMessages(5);
      manager.createSnapshot("session-1", messages, "Owl");

      const freshManager = new TimelineManager(workspacePath);

      vi.mocked(existsSync).mockReturnValue(true);
      vi.mocked(readFileSync).mockReturnValue(
        JSON.stringify({
          snapshots: [
            {
              id: "snap-1",
              sessionId: "session-1",
              messageIndex: 5,
              messages,
              metadata: { owlName: "Owl", snapshotAt: "2024-01-01T00:00:00Z" },
            },
          ],
          forks: [],
        }),
      );

      await freshManager.load();

      const timeline = freshManager.getTimeline("session-1");
      expect(timeline).not.toBeNull();
      expect(timeline!.snapshots).toHaveLength(1);
    });
  });
});
