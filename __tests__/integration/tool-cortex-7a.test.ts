import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";
import { createWebUnifiedTool } from "../../src/tools/web-unified.js";
import { createMemoryUnifiedTool } from "../../src/tools/memory-unified.js";
import type { SubGoal } from "../../src/engine/types.js";

describe("Phase 7a Tool Cortex — integration", () => {
  describe("Narration + EventBus wiring", () => {
    it("narration fires on tool:start and is silent on tool:goal_advance", () => {
      const startMsg = formatToolEvent({
        type: "tool:start",
        toolName: "duckduckgo_search",
        args: { query: "test" },
        turnId: "t1",
      });
      expect(startMsg).toContain("Searching");

      const advanceMsg = formatToolEvent({
        type: "tool:goal_advance",
        toolName: "duckduckgo_search",
        subGoal: "find info",
        verdict: "ADVANCES",
      });
      expect(advanceMsg).toBeNull();
    });
  });

  describe("ToolRegistry — platform guard + deprecated filter", () => {
    it("deprecated tools are excluded from getAllDefinitions()", () => {
      const registry = new ToolRegistry();
      registry.register({
        definition: { name: "active_tool", description: "x", parameters: { type: "object", properties: {} } },
        category: "filesystem" as any,
        execute: async () => "ok",
      });
      registry.register({
        definition: { name: "old_tool", description: "y", parameters: { type: "object", properties: {} }, deprecated: true },
        category: "filesystem" as any,
        execute: async () => "ok",
      });
      const names = registry.getAllDefinitions().map(d => d.name);
      expect(names).toContain("active_tool");
      expect(names).not.toContain("old_tool");
    });

    it("deprecated tools are still callable via execute()", async () => {
      const registry = new ToolRegistry();
      registry.register({
        definition: { name: "old_tool", description: "y", parameters: { type: "object", properties: {} }, deprecated: true },
        category: "filesystem" as any,
        execute: async () => "still works",
      });
      const result = await registry.execute("old_tool", {}, { cwd: "/" });
      expect(result).toBe("still works");
    });
  });

  describe("GAV hook end-to-end", () => {
    it("GAV emits goal_blocked and appends warning when verifier returns BLOCKED", async () => {
      const registry = new ToolRegistry();
      const bus = new GatewayEventBus();
      registry.setEventBus(bus);

      const verifier = {
        verify: vi.fn().mockResolvedValue({
          verdict: "BLOCKED",
          reason: "Paywall",
          suggestion: "try a different URL",
        }),
      } as unknown as GoalVerifier;
      registry.setGoalVerifier(verifier);

      registry.register({
        definition: { name: "paywall_tool", description: "test", parameters: { type: "object", properties: {} } },
        category: "web" as any,
        execute: async () => "Subscribe to read more",
      });

      const blockedHandler = vi.fn();
      bus.on("tool:goal_blocked", blockedHandler);

      const subGoal: SubGoal = { id: "sg-1", description: "Find article", status: "in_progress", dependsOn: [] };
      const result = await registry.execute("paywall_tool", {}, {
        cwd: "/",
        engineContext: { activeSubGoal: subGoal, userMessage: "find the article" } as any,
      });

      expect(blockedHandler).toHaveBeenCalled();
      expect(result).toContain("tool_result_warning");
      expect(result).toContain("BLOCKED");
    });
  });

  describe("Unified tools", () => {
    it("web tool has capabilities tag", () => {
      const tool = createWebUnifiedTool({});
      expect(tool.definition.capabilities).toContain("web_search");
    });

    it("memory tool has capabilities tag", () => {
      const tool = createMemoryUnifiedTool({});
      expect(tool.definition.capabilities).toContain("memory_search");
    });

    it("web tool name is 'web' (not 'duckduckgo_search')", () => {
      const tool = createWebUnifiedTool({});
      expect(tool.definition.name).toBe("web");
    });
  });
});
