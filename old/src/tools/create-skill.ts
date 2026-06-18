import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";

const NAME_RE = /^[a-z][a-z0-9_]*$/;

export interface CreateSkillInput {
  name: string;       // snake_case, lowercase
  description: string; // max 64 chars
  instructions: string; // markdown body for the SKILL.md
}

export class CreateSkillTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "create_skill",
    description:
      "Create a new SKILL.md skill file. Use when the user wants to teach you a new repeatable behaviour or workflow.",
    parameters: {
      type: "object",
      properties: {
        name: {
          type: "string",
          description: "Skill name in snake_case (lowercase letters, digits, and underscores only).",
        },
        description: {
          type: "string",
          description: "Brief description (max 64 characters) of what the skill does.",
        },
        instructions: {
          type: "string",
          description: "Markdown instructions for the skill. Should include steps, examples, or guidelines.",
        },
      },
      required: ["name", "description", "instructions"],
    },
  };

  category = "filesystem" as const;
  source = "builtin";

  constructor(private skillsDir?: string) {}

  private resolveDir(): string {
    return this.skillsDir ?? join(homedir(), ".stackowl", "skills");
  }

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const input = args as unknown as CreateSkillInput;

    log.tool.debug("create_skill.execute: entry", { name: input.name });

    if (!NAME_RE.test(input.name)) {
      const err = new Error(
        `Invalid name "${input.name}". Use lowercase letters, digits, and underscores only.`
      );
      log.tool.error("create_skill.execute: invalid name", err, { name: input.name });
      throw err;
    }

    if (input.description.length > 64) {
      const err = new Error(
        `Description too long (${input.description.length} chars). Keep it under 64.`
      );
      log.tool.error("create_skill.execute: description too long", err, { descLen: input.description.length });
      throw err;
    }

    if (!input.instructions || input.instructions.trim().length < 10) {
      const err = new Error("Instructions must be at least 10 characters.");
      log.tool.error("create_skill.execute: instructions too short", err, { instructionsLen: input.instructions?.length ?? 0 });
      throw err;
    }

    const skillDir = join(this.resolveDir(), input.name);
    const skillPath = join(skillDir, "SKILL.md");

    try {
      await mkdir(skillDir, { recursive: true });

      const content = [
        "---",
        `name: ${input.name}`,
        `description: ${input.description}`,
        "---",
        "",
        input.instructions.trim(),
        "",
      ].join("\n");

      await writeFile(skillPath, content, "utf-8");

      log.tool.info("create_skill.execute: skill created", { path: skillPath, skillName: input.name });
      return `Skill "${input.name}" created at ${skillPath}. It will be available on next message.`;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.tool.error("create_skill.execute: file write failed", err, { skillPath });
      throw new Error(`Failed to create skill: ${msg}`);
    }
  }
}
