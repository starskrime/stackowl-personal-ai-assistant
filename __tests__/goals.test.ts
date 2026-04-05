import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { GoalGraph } from "../src/goals/graph.js";
import type {
  ModelProvider,
  ChatResponse,
  ChatMessage,
} from "../src/providers/base.js";
import { rm } from "node:fs/promises";
import { join } from "node:path";

const { mockReadFile, mockWriteFile, mockMkdir, mockExistsSync, mockRm } =
  vi.hoisted(() => ({
    mockReadFile: vi.fn(),
    mockWriteFile: vi.fn(),
    mockMkdir: vi.fn(),
    mockExistsSync: vi.fn(),
    mockRm: vi.fn(),
  }));

vi.mock("node:fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("node:fs/promises")>();
  return {
    ...actual,
    readFile: mockReadFile,
    writeFile: mockWriteFile,
    mkdir: mockMkdir,
    rm: mockRm,
  };
});

vi.mock("node:fs", () => ({
  existsSync: mockExistsSync,
}));

const createMockProvider = (
  overrides?: Partial<ModelProvider>,
): ModelProvider => ({
  name: "mock",
  chat: vi.fn().mockResolvedValue({ content: "{}" } as ChatResponse),
  chatWithTools: vi.fn(),
  chatStream: vi.fn(),
  embed: vi.fn(),
  listModels: vi.fn(),
  healthCheck: vi.fn(),
  ...overrides,
});

const testSpace = join(__dirname, ".goals_test_workspace");

describe("GoalGraph", () => {
  let graph: GoalGraph;

  beforeEach(() => {
    vi.clearAllMocks();
    mockExistsSync.mockReturnValue(false);
    mockReadFile.mockResolvedValue("[]");
    mockMkdir.mockResolvedValue(undefined);
    mockWriteFile.mockResolvedValue(undefined);
    mockRm.mockResolvedValue(undefined);
    graph = new GoalGraph(testSpace);
  });

  afterEach(async () => {
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  });

  describe("load", () => {
    it("should load goals from file", async () => {
      const storedGoals = [
        {
          id: "goal_1",
          title: "Test Goal",
          description: "Description",
          status: "active",
          priority: "high",
          subGoalIds: [],
          dependsOn: [],
          progress: 50,
          milestones: [],
          mentionedInSessions: [],
          lastActiveAt: Date.now(),
          createdAt: Date.now(),
          updatedAt: Date.now(),
          tags: [],
        },
      ];
      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockResolvedValue(JSON.stringify(storedGoals));

      await graph.load();

      const all = graph.getAll();
      expect(all).toHaveLength(1);
      expect(all[0].id).toBe("goal_1");
      expect(all[0].title).toBe("Test Goal");
    });

    it("should not reload if already loaded", async () => {
      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockResolvedValue("[]");

      await graph.load();
      await graph.load();

      expect(mockReadFile).toHaveBeenCalledTimes(1);
    });

    it("should handle missing file gracefully", async () => {
      mockExistsSync.mockReturnValue(false);

      await expect(graph.load()).resolves.not.toThrow();
      expect(graph.getAll()).toHaveLength(0);
    });

    it("should handle corrupt JSON gracefully", async () => {
      mockExistsSync.mockReturnValue(true);
      mockReadFile.mockRejectedValue(new Error("File read error"));

      await expect(graph.load()).resolves.not.toThrow();
    });
  });

  describe("save", () => {
    it("should create directory if it does not exist", async () => {
      mockExistsSync.mockReturnValue(false);
      mockMkdir.mockResolvedValue(undefined);

      await graph.save();

      expect(mockMkdir).toHaveBeenCalledWith(expect.stringContaining("goals"), {
        recursive: true,
      });
    });

    it("should write goals to file", async () => {
      mockExistsSync.mockReturnValue(true);

      graph.addGoal({
        title: "Save Test",
        description: "Testing save",
        priority: "medium",
      });

      await graph.save();

      expect(mockWriteFile).toHaveBeenCalledWith(
        expect.stringContaining("goal-graph.json"),
        expect.any(String),
        "utf-8",
      );

      const writtenContent = mockWriteFile.mock.calls[0][1] as string;
      const parsed = JSON.parse(writtenContent);
      expect(parsed).toHaveLength(1);
      expect(parsed[0].title).toBe("Save Test");
    });
  });

  describe("addGoal", () => {
    it("should add a new goal with all fields", () => {
      const goal = graph.addGoal({
        title: "New Goal",
        description: "Goal description",
        priority: "high",
        milestones: ["Step 1", "Step 2"],
        sessionId: "session_123",
      });

      expect(goal.id).toMatch(/^goal_/);
      expect(goal.title).toBe("New Goal");
      expect(goal.description).toBe("Goal description");
      expect(goal.status).toBe("active");
      expect(goal.priority).toBe("high");
      expect(goal.progress).toBe(0);
      expect(goal.milestones).toHaveLength(2);
      expect(goal.milestones[0].completed).toBe(false);
      expect(goal.mentionedInSessions).toContain("session_123");
    });

    it("should generate unique IDs for milestones", () => {
      const goal = graph.addGoal({
        title: "Goal",
        description: "Desc",
        priority: "low",
        milestones: ["M1", "M2", "M3"],
      });

      const ids = goal.milestones.map((m) => m.id);
      expect(new Set(ids).size).toBe(3);
      ids.forEach((id) => expect(id).toMatch(/^ms_/));
    });

    it("should wire goal to parent when parentId is provided", () => {
      const parent = graph.addGoal({
        title: "Parent Goal",
        description: "Parent",
        priority: "high",
      });

      const child = graph.addGoal({
        title: "Child Goal",
        description: "Child",
        priority: "medium",
        parentId: parent.id,
      });

      expect(child.parentId).toBe(parent.id);
      expect(parent.subGoalIds).toContain(child.id);
    });

    it("should handle non-existent parent gracefully", () => {
      graph.addGoal({
        title: "Orphan",
        description: "No parent",
        priority: "low",
        parentId: "non-existent",
      });

      expect(graph.getAll()).toHaveLength(1);
    });
  });

  describe("updateGoalStatus", () => {
    it("should update goal status", () => {
      const goal = graph.addGoal({
        title: "Status Test",
        description: "Test",
        priority: "medium",
      });

      graph.updateGoalStatus(goal.id, "completed");

      const updated = graph.getAll()[0];
      expect(updated.status).toBe("completed");
      expect(updated.progress).toBe(100);
    });

    it("should set blocked reason when status is blocked", () => {
      const goal = graph.addGoal({
        title: "Blocked Test",
        description: "Test",
        priority: "high",
      });

      graph.updateGoalStatus(goal.id, "blocked", "Waiting for approval");

      const updated = graph.getAll()[0];
      expect(updated.status).toBe("blocked");
      expect(updated.blockedReason).toBe("Waiting for approval");
    });

    it("should do nothing for non-existent goal", () => {
      expect(() =>
        graph.updateGoalStatus("non-existent", "completed"),
      ).not.toThrow();
    });
  });

  describe("completeMilestone", () => {
    it("should mark milestone as completed and update progress", () => {
      const goal = graph.addGoal({
        title: "Milestone Test",
        description: "Test",
        priority: "high",
        milestones: ["Step 1", "Step 2", "Step 3"],
      });

      graph.completeMilestone(goal.id, "Step 1");

      const updated = graph.getAll()[0];
      const milestone = updated.milestones.find((m) =>
        m.description.includes("Step 1"),
      );
      expect(milestone?.completed).toBe(true);
      expect(milestone?.completedAt).toBeDefined();
      expect(updated.progress).toBe(33);
    });

    it("should ignore already completed milestone", () => {
      const goal = graph.addGoal({
        title: "Double Complete",
        description: "Test",
        priority: "low",
        milestones: ["Step 1"],
      });

      graph.completeMilestone(goal.id, "Step 1");
      graph.completeMilestone(goal.id, "Step 1");

      const updated = graph.getAll()[0];
      expect(updated.progress).toBe(100);
    });

    it("should do nothing for non-existent goal", () => {
      expect(() =>
        graph.completeMilestone("non-existent", "Step 1"),
      ).not.toThrow();
    });

    it("should handle case-insensitive milestone matching", () => {
      const goal = graph.addGoal({
        title: "Case Test",
        description: "Test",
        priority: "medium",
        milestones: ["UPPERCASE STEP"],
      });

      graph.completeMilestone(goal.id, "uppercase step");

      const updated = graph.getAll()[0];
      expect(updated.milestones[0].completed).toBe(true);
    });
  });

  describe("recordMention", () => {
    it("should update lastActiveAt and add session", async () => {
      const goal = graph.addGoal({
        title: "Mention Test",
        description: "Test",
        priority: "medium",
      });

      const originalCreatedAt = goal.createdAt;
      await new Promise((r) => setTimeout(r, 10));

      graph.recordMention(goal.id, "session_456");

      const updated = graph.getAll()[0];
      expect(updated.lastActiveAt).toBeGreaterThanOrEqual(originalCreatedAt);
      expect(updated.mentionedInSessions).toContain("session_456");
    });

    it("should not duplicate session ID", () => {
      const goal = graph.addGoal({
        title: "Mention Test",
        description: "Test",
        priority: "medium",
        sessionId: "session_1",
      });

      graph.recordMention(goal.id, "session_1");

      const updated = graph.getAll()[0];
      expect(updated.mentionedInSessions).toHaveLength(1);
    });
  });

  describe("getAll", () => {
    it("should return all goals as array", () => {
      graph.addGoal({ title: "Goal 1", description: "D1", priority: "high" });
      graph.addGoal({ title: "Goal 2", description: "D2", priority: "low" });

      const all = graph.getAll();
      expect(all).toHaveLength(2);
    });
  });

  describe("getActive", () => {
    it("should return goals with active or in_progress status", () => {
      const g1 = graph.addGoal({
        title: "Active",
        description: "D",
        priority: "high",
      });
      const g2 = graph.addGoal({
        title: "In Progress",
        description: "D",
        priority: "medium",
      });
      const g3 = graph.addGoal({
        title: "Completed",
        description: "D",
        priority: "low",
      });

      graph.updateGoalStatus(g2.id, "in_progress");
      graph.updateGoalStatus(g3.id, "completed");

      const active = graph.getActive();
      expect(active).toHaveLength(2);
      expect(active.map((g) => g.id)).toContain(g1.id);
      expect(active.map((g) => g.id)).toContain(g2.id);
    });
  });

  describe("getBlocked", () => {
    it("should return only blocked goals", () => {
      graph.addGoal({ title: "Active", description: "D", priority: "high" });
      const g2 = graph.addGoal({
        title: "Blocked",
        description: "D",
        priority: "medium",
      });

      graph.updateGoalStatus(g2.id, "blocked", "Waiting on input");

      const blocked = graph.getBlocked();
      expect(blocked).toHaveLength(1);
      expect(blocked[0].id).toBe(g2.id);
      expect(blocked[0].blockedReason).toBe("Waiting on input");
    });
  });

  describe("getStale", () => {
    it("should return active goals not mentioned within threshold", () => {
      const goal = graph.addGoal({
        title: "Stale Goal",
        description: "Test",
        priority: "medium",
      });

      goal.lastActiveAt = Date.now() - 8 * 24 * 60 * 60 * 1000;

      const stale = graph.getStale(7);
      expect(stale).toHaveLength(1);
      expect(stale[0].id).toBe(goal.id);
    });

    it("should not return recently mentioned goals", () => {
      const goal = graph.addGoal({
        title: "Fresh Goal",
        description: "Test",
        priority: "medium",
      });

      goal.lastActiveAt = Date.now();

      const stale = graph.getStale(7);
      expect(stale).toHaveLength(0);
    });

    it("should use custom threshold", () => {
      graph.addGoal({
        title: "Custom Threshold",
        description: "Test",
        priority: "high",
      });

      const stale = graph.getStale(365);
      expect(stale).toHaveLength(0);
    });
  });

  describe("findByTitle", () => {
    it("should find goal by fuzzy title match", () => {
      graph.addGoal({
        title: "Launch startup website",
        description: "D",
        priority: "high",
      });

      const found = graph.findByTitle("startup website");
      expect(found).toBeDefined();
      expect(found?.title).toBe("Launch startup website");
    });

    it("should be case-insensitive", () => {
      graph.addGoal({
        title: "Test Goal",
        description: "D",
        priority: "medium",
      });

      const found = graph.findByTitle("TEST");
      expect(found).toBeDefined();
    });

    it("should return undefined for no match", () => {
      graph.addGoal({ title: "Some Goal", description: "D", priority: "low" });

      const found = graph.findByTitle("nonexistent");
      expect(found).toBeUndefined();
    });
  });

  describe("getTopPriority", () => {
    it("should return highest priority active goal", () => {
      graph.addGoal({ title: "Low", description: "D", priority: "low" });
      graph.addGoal({ title: "High", description: "D", priority: "high" });
      const critical = graph.addGoal({
        title: "Critical",
        description: "D",
        priority: "critical",
      });

      const top = graph.getTopPriority();
      expect(top?.id).toBe(critical.id);
    });

    it("should prioritize critical over high", () => {
      graph.addGoal({ title: "High", description: "D", priority: "high" });
      const critical = graph.addGoal({
        title: "Critical",
        description: "D",
        priority: "critical",
      });

      const top = graph.getTopPriority();
      expect(top?.id).toBe(critical.id);
    });

    it("should return first active goal if no priority match", () => {
      const goal = graph.addGoal({
        title: "Only",
        description: "D",
        priority: "low",
      });

      const top = graph.getTopPriority();
      expect(top?.id).toBe(goal.id);
    });

    it("should return undefined when no active goals", () => {
      const goal = graph.addGoal({
        title: "Done",
        description: "D",
        priority: "high",
      });
      graph.updateGoalStatus(goal.id, "completed");

      const top = graph.getTopPriority();
      expect(top).toBeUndefined();
    });
  });

  describe("toContextString", () => {
    it("should return empty string when no active goals", () => {
      expect(graph.toContextString()).toBe("");
    });

    it("should format active goals with milestones and blockers", () => {
      const goal = graph.addGoal({
        title: "Build Startup",
        description: "Launch it",
        priority: "critical",
        milestones: ["Design", "Code", "Deploy"],
      });

      graph.completeMilestone(goal.id, "Design");

      const context = graph.toContextString();
      expect(context).toContain("<user_goals>");
      expect(context).toContain("CRITICAL");
      expect(context).toContain("Build Startup");
      expect(context).toContain("✓ Design");
      expect(context).toContain("○ Code");
      expect(context).toContain("</user_goals>");
    });

    it("should not include blocked goals in active context string", () => {
      const goal = graph.addGoal({
        title: "Waiting",
        description: "Blocked",
        priority: "high",
      });
      graph.updateGoalStatus(goal.id, "blocked", "Waiting on API");

      const context = graph.toContextString();
      expect(context).not.toContain("Waiting");
      expect(context).not.toContain("BLOCKED");
    });

    it("should include blocked goals when using getBlocked", () => {
      const goal = graph.addGoal({
        title: "Waiting",
        description: "Blocked",
        priority: "high",
      });
      graph.updateGoalStatus(goal.id, "blocked", "Waiting on API");

      const blocked = graph.getBlocked();
      expect(blocked).toHaveLength(1);
      expect(blocked[0].blockedReason).toBe("Waiting on API");
    });

    it("should limit to 8 active goals", () => {
      for (let i = 0; i < 10; i++) {
        graph.addGoal({
          title: `Goal ${i}`,
          description: "D",
          priority: "medium",
        });
      }

      const context = graph.toContextString();
      const goalLines = context
        .split("\n")
        .filter((l) => l.includes("[MEDIUM]"));
      expect(goalLines.length).toBeLessThanOrEqual(8);
    });
  });

  describe("extractFromConversation", () => {
    let mockProvider: ModelProvider;

    beforeEach(() => {
      mockProvider = createMockProvider();
    });

    it("should skip extraction for short messages", async () => {
      const messages: ChatMessage[] = [{ role: "user", content: "Hi" }];

      await graph.extractFromConversation(messages, mockProvider, "session_1");

      expect(mockProvider.chat).not.toHaveBeenCalled();
    });

    it("should extract new goals from conversation", async () => {
      const extractionResult = {
        newGoals: [
          {
            title: "Learn TypeScript",
            description: "Master TypeScript fundamentals",
            priority: "high",
            milestones: ["Basics", "Advanced Types"],
          },
        ],
        goalUpdates: [],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: JSON.stringify(extractionResult),
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        {
          role: "user",
          content: "I want to learn TypeScript and master its fundamentals",
        },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_1");

      const all = graph.getAll();
      expect(all.some((g) => g.title === "Learn TypeScript")).toBe(true);
    });

    it("should apply status updates to existing goals", async () => {
      const existing = graph.addGoal({
        title: "Old Project",
        description: "Being updated",
        priority: "medium",
      });

      const extractionResult = {
        newGoals: [],
        goalUpdates: [
          {
            goalTitle: "Old Project",
            statusChange: "completed",
            progressDelta: 100,
          },
        ],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: JSON.stringify(extractionResult),
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        {
          role: "user",
          content: "I finished my old project and completed everything",
        },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_2");

      const updated = graph.getAll().find((g) => g.id === existing.id);
      expect(updated?.status).toBe("completed");
    });

    it("should complete milestones from extraction", async () => {
      const goal = graph.addGoal({
        title: "Multi-step Task",
        description: "A task",
        priority: "high",
        milestones: ["Step 1", "Step 2"],
      });

      const extractionResult = {
        newGoals: [],
        goalUpdates: [
          {
            goalTitle: "Multi-step Task",
            milestonesCompleted: ["Step 1"],
          },
        ],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: JSON.stringify(extractionResult),
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        { role: "user", content: "I completed step 1 of my multi-step task" },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_3");

      const updated = graph.getAll().find((g) => g.id === goal.id);
      const step1 = updated?.milestones.find((m) =>
        m.description.includes("Step 1"),
      );
      expect(step1?.completed).toBe(true);
    });

    it("should not duplicate existing goals", async () => {
      graph.addGoal({
        title: "Existing Goal",
        description: "Already there",
        priority: "low",
      });

      const extractionResult = {
        newGoals: [
          {
            title: "Existing Goal",
            description: "Duplicate",
            priority: "high",
            milestones: [],
          },
        ],
        goalUpdates: [],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: JSON.stringify(extractionResult),
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        { role: "user", content: "I want to work on my existing goal again" },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_4");

      const all = graph.getAll();
      expect(all.filter((g) => g.title === "Existing Goal")).toHaveLength(1);
    });

    it("should handle JSON wrapped in code blocks", async () => {
      const extractionResult = {
        newGoals: [
          {
            title: "Code Block Goal",
            description: "From code block",
            priority: "medium",
            milestones: [],
          },
        ],
        goalUpdates: [],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: "```json\n" + JSON.stringify(extractionResult) + "\n```",
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        {
          role: "user",
          content: "I need to work on something from the code block response",
        },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_5");

      const all = graph.getAll();
      expect(all.some((g) => g.title === "Code Block Goal")).toBe(true);
    });

    it("should handle LLM errors gracefully", async () => {
      mockProvider = createMockProvider({
        chat: vi.fn().mockRejectedValue(new Error("LLM error")),
      });

      const messages: ChatMessage[] = [
        {
          role: "user",
          content:
            "This is a longer message that should trigger extraction but will fail",
        },
      ];

      await expect(
        graph.extractFromConversation(messages, mockProvider, "session_6"),
      ).resolves.not.toThrow();
    });

    it("should save after extraction", async () => {
      const extractionResult = {
        newGoals: [
          {
            title: "New After Save",
            description: "Test",
            priority: "low",
            milestones: [],
          },
        ],
        goalUpdates: [],
      };

      mockProvider = createMockProvider({
        chat: vi.fn().mockResolvedValue({
          content: JSON.stringify(extractionResult),
        } as ChatResponse),
      });

      const messages: ChatMessage[] = [
        {
          role: "user",
          content:
            "Adding a new goal that should be saved after extraction process",
        },
      ];

      await graph.extractFromConversation(messages, mockProvider, "session_7");

      expect(mockWriteFile).toHaveBeenCalled();
    });
  });
});
