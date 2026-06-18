/**
 * StackOwl — Synthesized Capabilities Catalog Tool
 *
 * Allows owls to query which synthesized tools and skills have been
 * created (either in this session or loaded from disk at startup).
 * Used to avoid duplicating synthesis work and understand current capabilities.
 */

import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const SynthesizedCatalogTool: ToolImplementation = {
  definition: {
    name: "list_synthesized_capabilities",
    description:
      "List all tools and skills that have been synthesized (either in this session or loaded from " +
      "disk at startup). Use this before synthesizing a new capability to avoid duplicating existing ones.",
    parameters: {
      type: "object",
      properties: {},
      required: [],
    },
    capabilities: ["introspection"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  source: "builtin",

  async execute(
    _args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    log.synthesis.debug("list_synthesized_capabilities.execute: entry");

    const registry = context.engineContext?.toolRegistry;
    if (!registry || typeof registry.getAll !== "function") {
      log.synthesis.warn(
        "list_synthesized_capabilities.execute: registry or getAll not available",
      );
      return JSON.stringify({
        tools: [],
        total: 0,
        note: "Tool registry not available.",
      });
    }

    const all = registry.getAll();
    const synthesizedTools = all
      .filter((t) => t.source === "synthesized")
      .map((t) => t.definition.name);

    const result = {
      tools: synthesizedTools,
      total: synthesizedTools.length,
      note:
        synthesizedTools.length === 0
          ? "No synthesized tools in current session."
          : `${synthesizedTools.length} synthesized tool(s) available.`,
    };

    log.synthesis.debug("list_synthesized_capabilities.execute: exit", {
      count: synthesizedTools.length,
    });
    return JSON.stringify(result, null, 2);
  },
};
