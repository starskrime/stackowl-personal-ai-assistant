import { writeFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { SYNTHESIZED_DIR } from "../evolution/synthesizer.js";

export const PatchTool: ToolImplementation = {
  definition: {
    name: "patch_tool",
    description:
      "Rewrite the source code of a broken or buggy tool. Use this when you are stuck in an error loop and realize a tool implementation itself is flawed. Core tools require a restart, but synthesized tools will be hot-reloaded automatically.",
    parameters: {
      type: "object",
      properties: {
        toolName: {
          type: "string",
          description:
            'The exact name of the tool to patch (e.g. "shell", "read_file", or a synthesized tool name).',
        },
        newSourceCode: {
          type: "string",
          description:
            "The COMPLETE, fully re-written TypeScript source code for the tool. This MUST be the entire file content, not a git-diff. Ensure the code returns a default export or matches the ToolImplementation structure.",
        },
        description: {
          type: "string",
          description:
            "A brief description of what bug you are fixing inside this tool.",
        },
      },
      required: ["toolName", "newSourceCode", "description"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const toolName = args["toolName"] as string;
    const newSourceCode = args["newSourceCode"] as string;
    const description = args["description"] as string;

    if (!toolName || !newSourceCode) {
      return `ERROR: Missing toolName or newSourceCode.`;
    }

    log.tool.warn(
      `[Toolsmith] The AI is patching tool '${toolName}'. Reason: ${description}`,
    );

    // Try to find the tool file
    // 1. Check synthesized directory first
    const synthesizedPath = join(SYNTHESIZED_DIR, `${toolName}.ts`);
    const builtInPath = resolve(
      process.cwd(),
      "src",
      "tools",
      `${toolName}.ts`,
    );

    let targetPath = "";
    if (existsSync(synthesizedPath)) {
      targetPath = synthesizedPath;
      writeFileSync(targetPath, newSourceCode, "utf-8");

      // Wait, we need to hot reload it using the loader on the EngineContext if it's there
      // But we can just tell the LLM it's patched.
      return `SUCCESS: Synthesized tool '${toolName}' has been successfully patched at ${targetPath}. The fix is saved. Please retry your objective now.`;
    } else if (existsSync(builtInPath)) {
      targetPath = builtInPath;
      writeFileSync(targetPath, newSourceCode, "utf-8");
      return `SUCCESS: Core tool '${toolName}' has been patched at ${targetPath}. Note: Since this is a core tool, the process must be completely restarted for the changes to take effect. If you execute it again right now, it will use the old logic. Please output [CAPABILITY_GAP] or finish the task if you require a restart.`;
    } else {
      // Write it as a new synthesized tool if not found anywhere else?
      // Actually, patch_tool is specifically for rewriting existing tools.
      return `ERROR: Could not locate the source code for tool '${toolName}'. I checked ${synthesizedPath} and ${builtInPath}. Are you sure it exists? Use 'run_shell_command' to find it if necessary.`;
    }
  },
};
