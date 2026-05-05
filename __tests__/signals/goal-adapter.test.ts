import { describe, it, expect } from "vitest";
import {
  signalToVerifyArgs,
  goalToSubGoal,
} from "../../src/signals/goal-adapter.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

const goal: Goal = {
  id: "g1",
  title: "Ship Element 16b",
  description: "Unify perches and ambient",
  status: "active",
  priority: "high",
  subGoalIds: [],
  dependsOn: [],
  progress: 30,
  milestones: [],
  mentionedInSessions: [],
  lastActiveAt: 0,
  createdAt: 0,
  updatedAt: 0,
  tags: [],
};

const sig: ContextSignal = {
  id: "s1",
  source: "git",
  priority: "high",
  title: "12 uncommitted files in src/signals/",
  content: "M src/signals/pool.ts",
  timestamp: Date.now(),
  ttlMs: 60_000,
};

describe("goalToSubGoal", () => {
  it("converts a Goal to the SubGoal shape GoalVerifier expects", () => {
    const sg = goalToSubGoal(goal);
    expect(sg.id).toBe("g1");
    expect(sg.description).toBe("Ship Element 16b");
    expect(sg.status).toBe("in_progress");
    expect(sg.dependsOn).toEqual([]);
  });
});

describe("signalToVerifyArgs", () => {
  it("packages signal + goal as VerifyArgs", () => {
    const args = signalToVerifyArgs(sig, goal, "user is editing src/signals/");
    expect(args.toolName).toBe("ambient_signal");
    expect(args.toolArgs).toEqual({ source: "git", priority: "high" });
    expect(args.userMessage).toBe("user is editing src/signals/");
    const env = JSON.parse(args.toolResult);
    expect(env.success).toBe(true);
    expect(env.data).toContain("12 uncommitted files");
  });

  it("defaults userMessage to empty string when omitted", () => {
    const args = signalToVerifyArgs(sig, goal);
    expect(args.userMessage).toBe("");
  });
});
