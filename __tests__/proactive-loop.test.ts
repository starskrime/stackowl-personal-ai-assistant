import { describe, it, expect, vi } from "vitest";
import { ProactiveIntentionLoop } from "../src/intent/proactive-loop.js";
import type { CommitmentTracker } from "../src/intent/commitment-tracker.js";
import type { IntentStateMachine } from "../src/intent/state-machine.js";
import type { GoalGraph } from "../src/goals/graph.js";
import type { ContextMesh } from "../src/ambient/mesh.js";
import type { TrackedCommitment } from "../src/intent/commitment-tracker.js";
import type { Intent } from "../src/intent/types.js";

function makeMockCommitment(
  overrides: Partial<TrackedCommitment> = {},
): TrackedCommitment {
  const now = Date.now();
  return {
    id: "c1",
    intentId: "intent_1",
    sessionId: "session_1",
    statement: "Remember to check in",
    deadline: now - 1000,
    followUpMessage: "Hey, checking in!",
    context: "User asked",
    status: "pending",
    createdAt: now - 86400000,
    ...overrides,
  };
}

function makeMockIntent(overrides: Partial<Intent> = {}): Intent {
  const now = Date.now();
  return {
    id: "intent_1",
    description: "Test intent",
    rawQuery: "Test query",
    type: "task",
    status: "in_progress",
    checkpoints: [],
    commitments: [],
    sessionId: "session_1",
    createdAt: now - 3600000,
    updatedAt: now,
    lastActiveAt: now - 3600000,
    ...overrides,
  };
}

function makeMockGoal(
  overrides: Partial<{
    id: string;
    title: string;
    progress: number;
    lastActiveAt: number;
  }> = {},
): { id: string; title: string; progress: number; lastActiveAt: number } {
  return {
    id: "goal_1",
    title: "Test goal",
    progress: 50,
    lastActiveAt: Date.now() - 86400000 * 4,
    ...overrides,
  };
}

function makeMockSignal(
  overrides: Partial<{
    id: string;
    title: string;
    content: string | undefined;
    priority: string;
    source: string;
  }> = {},
): {
  id: string;
  title: string;
  content: string | undefined;
  priority: string;
  source: string;
} {
  return {
    id: "sig_1",
    title: "Important signal",
    content: "Something happened",
    priority: "high",
    source: "test",
    ...overrides,
  };
}

function createMockTracker(
  overrides: Partial<{
    getDueValues: TrackedCommitment[];
    getPendingValues: TrackedCommitment[];
  }> = {},
): CommitmentTracker {
  return {
    getDue: vi.fn().mockReturnValue(overrides.getDueValues ?? []),
    getPending: vi.fn().mockReturnValue(overrides.getPendingValues ?? []),
    track: vi.fn(),
    markSent: vi.fn(),
    markAcknowledged: vi.fn(),
    markDismissed: vi.fn(),
    markExpired: vi.fn(),
    toContextString: vi.fn().mockReturnValue(""),
  };
}

function createMockISM(
  overrides: Partial<{
    getStaleValues: Intent[];
    getActiveValues: Intent[];
    getPendingCommitmentsValues: Array<{ intent: Intent; commitment: unknown }>;
  }> = {},
): IntentStateMachine {
  return {
    getStale: vi.fn().mockReturnValue(overrides.getStaleValues ?? []),
    getActive: vi.fn().mockReturnValue(overrides.getActiveValues ?? []),
    getPendingCommitments: vi
      .fn()
      .mockReturnValue(overrides.getPendingCommitmentsValues ?? []),
  } as unknown as IntentStateMachine;
}

function createMockGoals(
  goals: ReturnType<typeof makeMockGoal>[] = [],
): GoalGraph {
  return {
    getStale: vi.fn().mockReturnValue(goals),
  } as unknown as GoalGraph;
}

function createMockMesh(
  signals: ReturnType<typeof makeMockSignal>[] = [],
): ContextMesh {
  return {
    getState: vi.fn().mockReturnValue({ signals }),
  } as unknown as ContextMesh;
}

describe("ProactiveIntentionLoop", () => {
  describe("evaluate()", () => {
    it("returns null when no trackers are provided", () => {
      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        undefined,
        undefined,
      );
      expect(loop.evaluate()).toBeNull();
    });

    it("returns due commitment with priority 100", () => {
      const mockTracker = createMockTracker({
        getDueValues: [makeMockCommitment()],
      });

      const loop = new ProactiveIntentionLoop(
        mockTracker,
        undefined,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.type).toBe("commitment");
      expect(result!.priority).toBe(100);
      expect(result!.message).toBe("Hey, checking in!");
    });

    it("returns stale intent with priority 80", () => {
      const mockISM = createMockISM({
        getStaleValues: [
          makeMockIntent({
            status: "in_progress",
            lastActiveAt: Date.now() - 3600000,
          }),
        ],
      });

      const loop = new ProactiveIntentionLoop(
        undefined,
        mockISM,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.type).toBe("stale_intent");
      expect(result!.priority).toBe(80);
    });

    it("returns stale goal with priority 60", () => {
      const mockGoals = createMockGoals([makeMockGoal()]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        mockGoals,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.type).toBe("stale_goal");
      expect(result!.priority).toBe(60);
    });

    it("returns ambient signal with priority 50", () => {
      const mockMesh = createMockMesh([makeMockSignal({ priority: "high" })]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        undefined,
        mockMesh,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.type).toBe("ambient_signal");
      expect(result!.priority).toBe(50);
    });

    it("respects priority order: commitment > stale_intent > stale_goal > ambient", () => {
      const mockTracker = createMockTracker({
        getDueValues: [makeMockCommitment()],
      });
      const mockISM = createMockISM({
        getStaleValues: [makeMockIntent({ status: "in_progress" })],
      });

      const loop = new ProactiveIntentionLoop(
        mockTracker,
        mockISM,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result!.type).toBe("commitment");
      expect(result!.priority).toBe(100);
    });

    it("only returns critical/high ambient signals", () => {
      const mockMesh = createMockMesh([makeMockSignal({ priority: "low" })]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        undefined,
        mockMesh,
      );
      expect(loop.evaluate()).toBeNull();
    });

    it("handles goal graph errors gracefully", () => {
      const brokenGoals = {
        getStale: vi.fn().mockImplementation(() => {
          throw new Error("Graph error");
        }),
      } as unknown as GoalGraph;

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        brokenGoals,
        undefined,
      );
      expect(loop.evaluate()).toBeNull();
    });

    it("limits stale goals to 2", () => {
      const mockGoals = createMockGoals([
        makeMockGoal({ id: "g1", title: "Goal 1" }),
        makeMockGoal({ id: "g2", title: "Goal 2" }),
        makeMockGoal({ id: "g3", title: "Goal 3" }),
      ]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        mockGoals,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.type).toBe("stale_goal");
    });
  });

  describe("getPendingSummary()", () => {
    it("returns nothing pending when all trackers return empty", () => {
      const mockTracker = createMockTracker();
      const mockISM = createMockISM();
      const mockGoals = createMockGoals([]);

      const loop = new ProactiveIntentionLoop(
        mockTracker,
        mockISM,
        mockGoals,
        undefined,
      );
      expect(loop.getPendingSummary()).toBe("Proactive: nothing pending");
    });

    it("counts pending commitments", () => {
      const mockTracker = createMockTracker({
        getPendingValues: [makeMockCommitment(), makeMockCommitment()],
      });

      const loop = new ProactiveIntentionLoop(
        mockTracker,
        undefined,
        undefined,
        undefined,
      );
      expect(loop.getPendingSummary()).toContain("2 pending commitment(s)");
    });

    it("counts stale intents", () => {
      const mockISM = createMockISM({
        getStaleValues: [makeMockIntent(), makeMockIntent()],
      });

      const loop = new ProactiveIntentionLoop(
        undefined,
        mockISM,
        undefined,
        undefined,
      );
      expect(loop.getPendingSummary()).toContain("2 stale intent(s)");
    });

    it("handles goal errors gracefully in summary", () => {
      const mockGoals = {
        getStale: vi.fn().mockImplementation(() => {
          throw new Error("fail");
        }),
      } as unknown as GoalGraph;

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        mockGoals,
        undefined,
      );
      expect(loop.getPendingSummary()).toBeTruthy();
    });
  });

  describe("stale intent message building", () => {
    it("includes intent description in stale intent message", () => {
      const mockISM = createMockISM({
        getStaleValues: [
          makeMockIntent({
            status: "in_progress",
            description: "Complete project X",
          }),
        ],
      });

      const loop = new ProactiveIntentionLoop(
        undefined,
        mockISM,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.message).toContain("Complete project X");
    });

    it("includes follow-up message for stale goals", () => {
      const mockGoals = createMockGoals([
        makeMockGoal({ title: "Finish report" }),
      ]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        mockGoals,
        undefined,
      );
      const result = loop.evaluate();

      expect(result).not.toBeNull();
      expect(result!.message).toContain("Finish report");
    });

    it("returns correct metadata for commitment type", () => {
      const mockTracker = createMockTracker({
        getDueValues: [makeMockCommitment({ id: "c_123", intentId: "i_456" })],
      });

      const loop = new ProactiveIntentionLoop(
        mockTracker,
        undefined,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result!.metadata).toEqual({
        commitmentId: "c_123",
        intentId: "i_456",
      });
    });

    it("returns correct metadata for stale_intent type", () => {
      const mockISM = createMockISM({
        getStaleValues: [makeMockIntent({ id: "intent_999" })],
      });

      const loop = new ProactiveIntentionLoop(
        undefined,
        mockISM,
        undefined,
        undefined,
      );
      const result = loop.evaluate();

      expect(result!.metadata).toEqual({ intentId: "intent_999" });
    });

    it("returns correct metadata for ambient_signal type", () => {
      const signal = makeMockSignal({ id: "sig_888", source: "monitor" });
      const mockMesh = createMockMesh([signal]);

      const loop = new ProactiveIntentionLoop(
        undefined,
        undefined,
        undefined,
        mockMesh,
      );
      const result = loop.evaluate();

      expect(result!.metadata).toEqual({
        signalId: "sig_888",
        source: "monitor",
      });
    });
  });
});
