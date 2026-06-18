import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import type { ToolImplementation } from "../../src/tools/registry.js";
import type { SubGoal } from "../../src/engine/types.js";

const subGoal: SubGoal = {
  id: "sg-1",
  description: "Find the TypeScript version",
  status: "in_progress",
  dependsOn: [],
};

function makeVerifier(verdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL"): GoalVerifier {
  return {
    verify: vi.fn().mockResolvedValue({ verdict, reason: "test", suggestion: verdict === "BLOCKED" ? "try something else" : undefined }),
  } as unknown as GoalVerifier;
}

function makeTool(name: string): ToolImplementation {
  return {
    definition: { name, description: "test", parameters: { type: "object", properties: {} } },
    category: "filesystem" as any,
    execute: async () => "tool result",
  };
}

describe("ToolRegistry GAV hook", () => {
  it("calls verifier when activeSubGoal is in engineContext", async () => {
    const registry = new ToolRegistry();
    const verifier = makeVerifier("ADVANCES");
    registry.setGoalVerifier(verifier);
    registry.register(makeTool("test_tool"));

    await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: subGoal, userMessage: "test question" } as any,
    });

    expect(verifier.verify).toHaveBeenCalledWith(expect.objectContaining({
      toolName: "test_tool",
      subGoal,
      userMessage: "test question",
    }));
  });

  it("does NOT call verifier when no activeSubGoal", async () => {
    const registry = new ToolRegistry();
    const verifier = makeVerifier("ADVANCES");
    registry.setGoalVerifier(verifier);
    registry.register(makeTool("test_tool"));

    await registry.execute("test_tool", {}, { cwd: "/" });

    expect(verifier.verify).not.toHaveBeenCalled();
  });

  it("emits tool:goal_advance when verdict is ADVANCES", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);
    registry.setGoalVerifier(makeVerifier("ADVANCES"));
    registry.register(makeTool("test_tool"));

    const handler = vi.fn();
    bus.on("tool:goal_advance", handler);

    await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: subGoal, userMessage: "q" } as any,
    });

    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "tool:goal_advance", verdict: "ADVANCES" }));
  });

  it("emits tool:goal_blocked when verdict is BLOCKED", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);
    registry.setGoalVerifier(makeVerifier("BLOCKED"));
    registry.register(makeTool("test_tool"));

    const handler = vi.fn();
    bus.on("tool:goal_blocked", handler);

    await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: subGoal, userMessage: "q" } as any,
    });

    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "tool:goal_blocked", suggestion: "try something else" }));
  });

  it("appends BLOCKED warning to result when verdict is BLOCKED", async () => {
    const registry = new ToolRegistry();
    registry.setGoalVerifier(makeVerifier("BLOCKED"));
    registry.register(makeTool("test_tool"));

    const result = await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: subGoal, userMessage: "q" } as any,
    });

    expect(result).toContain("tool_result_warning");
  });

  it("appends PARTIAL warning to result when verdict is PARTIAL", async () => {
    const registry = new ToolRegistry();
    registry.setGoalVerifier(makeVerifier("PARTIAL"));
    registry.register(makeTool("test_tool"));

    const result = await registry.execute("test_tool", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: subGoal, userMessage: "q" } as any,
    });

    expect(result).toContain("tool_result_warning");
  });
});
