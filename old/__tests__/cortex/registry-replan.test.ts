/**
 * StackOwl — Element 7 T10 — Registry BLOCKED-verdict triggers ToolGraph replan
 *
 * On a BLOCKED verdict the registry should consult ToolGraph for a next-best
 * tool (single hop), execute it, and return its result. EdgeAccumulator
 * records the (from → to) success observation. Recursion is capped at one
 * hop to prevent infinite loops when every fallback also blocks.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolRegistry } from "../../src/tools/registry.js";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";

function makeRegistry(): { registry: ToolRegistry; db: MemoryDatabase } {
  const dir = mkdtempSync(join(tmpdir(), "reg-replan-"));
  const db = new MemoryDatabase(dir);
  const registry = new ToolRegistry();
  registry.setToolGraph(new ToolGraph(db));
  registry.setEdgeAccumulator(new EdgeAccumulator(db));
  return { registry, db };
}

const subGoalCtx = {
  engineContext: {
    sessionId: "s1",
    activeSubGoal: { id: "sg1", description: "fetch article" },
    userMessage: "read it",
  },
} as never;

describe("Registry — BLOCKED-verdict triggers replan", () => {
  let registry: ToolRegistry;
  let db: MemoryDatabase;

  beforeEach(() => {
    ({ registry, db } = makeRegistry());
  });

  it("auto-falls back to next-best tool when verifier returns BLOCKED", async () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
      )
      .run("web", "web_crawl", "web_fetch", 0.95, 100, 50);

    let blockOnce = true;
    registry.setGoalVerifier({
      verify: async ({ toolName }) => {
        if (toolName === "web" && blockOnce) {
          blockOnce = false;
          return { verdict: "BLOCKED", reason: "paywall" };
        }
        return { verdict: "ADVANCES", reason: "ok" };
      },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    registry.register({
      definition: {
        name: "web",
        description: "",
        parameters: { type: "object", properties: {} },
        capabilities: ["web_fetch"],
      },
      execute: async () => "behind paywall",
    });
    const crawlExec = vi.fn().mockResolvedValue("clean content");
    registry.register({
      definition: {
        name: "web_crawl",
        description: "",
        parameters: { type: "object", properties: {} },
        capabilities: ["web_fetch"],
      },
      execute: crawlExec,
    });

    const result = await registry.execute("web", {}, subGoalCtx);
    expect(crawlExec).toHaveBeenCalledOnce();
    expect(result).toContain("clean content");
  });

  it("does not replan when tool has no capability tag", async () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
      )
      .run("web", "web_crawl", "web_fetch", 0.95, 100, 50);

    registry.setGoalVerifier({
      verify: async () => ({ verdict: "BLOCKED", reason: "paywall" }),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    registry.register({
      definition: {
        name: "web",
        description: "",
        parameters: { type: "object", properties: {} },
        // no capabilities[] — replan must be skipped
      },
      execute: async () => "behind paywall",
    });
    const crawlExec = vi.fn().mockResolvedValue("clean content");
    registry.register({
      definition: {
        name: "web_crawl",
        description: "",
        parameters: { type: "object", properties: {} },
        capabilities: ["web_fetch"],
      },
      execute: crawlExec,
    });

    const result = await registry.execute("web", {}, subGoalCtx);
    expect(crawlExec).not.toHaveBeenCalled();
    expect(result).toContain("behind paywall");
    expect(result).toContain("BLOCKED");
  });

  it("caps replan recursion at one hop (no infinite loop)", async () => {
    db.rawDb
      .prepare(
        "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
      )
      .run("web", "web_crawl", "web_fetch", 0.95, 100, 50);

    registry.setGoalVerifier({
      verify: async () => ({ verdict: "BLOCKED", reason: "still blocked" }),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);

    const webExec = vi.fn().mockResolvedValue("blocked-1");
    const crawlExec = vi.fn().mockResolvedValue("blocked-2");
    registry.register({
      definition: {
        name: "web",
        description: "",
        parameters: { type: "object", properties: {} },
        capabilities: ["web_fetch"],
      },
      execute: webExec,
    });
    registry.register({
      definition: {
        name: "web_crawl",
        description: "",
        parameters: { type: "object", properties: {} },
        capabilities: ["web_fetch"],
      },
      execute: crawlExec,
    });

    const result = await registry.execute("web", {}, subGoalCtx);
    expect(webExec).toHaveBeenCalledOnce();
    expect(crawlExec).toHaveBeenCalledOnce();
    // Second hop must NOT trigger another replan — depth cap = 1
    expect(result).toContain("blocked-2");
    expect(result).toContain("BLOCKED");
  });
});
