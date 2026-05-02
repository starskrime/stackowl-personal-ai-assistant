import { describe, it, expect } from "vitest";
import type { TurnRequest, SubGoal } from "../../src/engine/types.js";

describe("TurnRequest subgoal extensions", () => {
  it("accepts optional activeSubGoal", () => {
    const subGoal: SubGoal = {
      id: "sg-1",
      description: "Find the current TypeScript version",
      status: "in_progress",
    };
    const turn: TurnRequest = {
      message: "test",
      activeSubGoal: subGoal,
    };
    expect(turn.activeSubGoal?.id).toBe("sg-1");
  });

  it("accepts optional userMessage field", () => {
    const turn: TurnRequest = {
      message: "test message",
      userMessage: "original user message",
    };
    expect(turn.userMessage).toBe("original user message");
  });

  it("TurnRequest is valid without activeSubGoal (backward compat)", () => {
    const turn: TurnRequest = {
      message: "test",
    };
    expect(turn.activeSubGoal).toBeUndefined();
    expect(turn.userMessage).toBeUndefined();
  });
});
