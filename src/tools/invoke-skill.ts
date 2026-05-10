import type { ToolImplementation, ToolContext } from "./registry.js";
import { toolError, toolSuccess } from "./tool-error.js";
import { log } from "../logger.js";

/**
 * Minimal interface for an executor that can run a named skill.
 * Injected at runtime; absent in tests.
 */
export interface SkillExecutor {
  executeByName(name: string, params: Record<string, unknown>): Promise<string>;
}

/**
 * invoke_skill — lets the LLM explicitly invoke a named skill by name.
 * Use when the owl already knows which skill applies and wants to run it
 * directly without relying on automatic skill injection.
 */
export function createInvokeSkillTool(
  skillExecutor?: SkillExecutor,
): ToolImplementation {
  return {
    definition: {
      name: "invoke_skill",
      description:
        "Explicitly invoke a named skill. Use when you know the exact skill name. " +
        "Example: invoke_skill with name='web-research'. " +
        "List available skills with the list_skills tool first if unsure.",
      parameters: {
        type: "object" as const,
        properties: {
          name: {
            type: "string",
            description: "Name of the skill to invoke (snake_case or kebab-case)",
          },
          params: {
            type: "string",
            description: "Optional JSON string of parameters to pass to the skill",
          },
        },
        required: ["name"],
      },
    },

    category: "cognitive" as const,
    source: "builtin",

    async execute(
      args: Record<string, unknown>,
      _ctx: ToolContext,
    ): Promise<string> {
      const name = (args["name"] as string | undefined)?.trim();
      log.tool.debug("invoke-skill.execute: entry", { name });
      if (!name) {
        return toolError("MISSING_ARG", "The `name` argument is required.");
      }

      let params: Record<string, unknown> = {};
      if (args["params"]) {
        try {
          params = JSON.parse(args["params"] as string);
        } catch (err) {
          // Non-fatal — proceed with empty params
          log.tool.warn("invoke-skill: params JSON parse failed, using empty params", err);
        }
      }

      if (!skillExecutor) {
        return toolError(
          "NO_EXECUTOR",
          "Skill executor not configured.",
          "Ask the administrator to wire a SkillExecutor into the tool registry.",
        );
      }

      try {
        log.tool.debug("invoke-skill.execute: calling skill", { name, paramKeys: Object.keys(params) });
        const result = await skillExecutor.executeByName(name, params);
        log.tool.debug("invoke-skill.execute: exit", { success: true, name, resultLen: result.length });
        return toolSuccess({ skillName: name, result });
      } catch (err) {
        log.tool.error("invoke-skill.execute: failed", err, { name });
        return toolError(
          "SKILL_FAILED",
          String(err),
          `Check that a skill named "${name}" exists and is loaded.`,
        );
      }
    },
  };
}
