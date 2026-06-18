import { describe, it, expect, vi } from "vitest";
import { SynthesizedCatalogTool } from "../../src/tools/synthesized-catalog.js";
import type { ToolContext, ToolImplementation } from "../../src/tools/registry.js";

function makeRegistry(tools: Array<{ name: string; source?: string }>) {
  return {
    getAll: () =>
      tools.map((t) => ({
        definition: {
          name: t.name,
          description: "desc",
          parameters: { type: "object", properties: {}, required: [] },
        },
        source: t.source ?? "builtin",
        execute: async () => "",
      })) as ToolImplementation[],
  };
}

describe("SynthesizedCatalogTool.execute", () => {
  it("returns synthesized tools from registry", async () => {
    const registry = makeRegistry([
      { name: "shell", source: "builtin" },
      { name: "synth_csv_parser", source: "synthesized" },
      { name: "synth_api_caller", source: "synthesized" },
    ]);
    const ctx: ToolContext = {
      cwd: "/workspace",
      engineContext: { toolRegistry: registry } as any,
    };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.tools).toHaveLength(2);
    expect(parsed.tools).toContain("synth_csv_parser");
    expect(parsed.tools).toContain("synth_api_caller");
    expect(parsed.tools).not.toContain("shell");
  });

  it("returns empty arrays when no synthesized tools exist", async () => {
    const registry = makeRegistry([{ name: "shell", source: "builtin" }]);
    const ctx: ToolContext = {
      cwd: "/workspace",
      engineContext: { toolRegistry: registry } as any,
    };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.tools).toHaveLength(0);
  });

  it("returns total count in summary", async () => {
    const registry = makeRegistry([
      { name: "synth_x", source: "synthesized" },
      { name: "synth_y", source: "synthesized" },
    ]);
    const ctx: ToolContext = {
      cwd: "/workspace",
      engineContext: { toolRegistry: registry } as any,
    };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.total).toBe(2);
  });

  it("handles missing registry gracefully", async () => {
    const ctx: ToolContext = { cwd: "/workspace" };
    const result = await SynthesizedCatalogTool.execute({}, ctx);
    const parsed = JSON.parse(result);
    expect(parsed.tools).toHaveLength(0);
    expect(typeof parsed.note).toBe("string");
  });
});
