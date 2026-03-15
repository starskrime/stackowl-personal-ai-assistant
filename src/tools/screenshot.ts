/**
 * StackOwl — Screenshot Tool
 *
 * Takes a screenshot of the screen or a specific window on macOS.
 * Returns the file path so the owl can send it via send_file.
 */

import { exec } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";

const execAsync = promisify(exec);

export const ScreenshotTool: ToolImplementation = {
  definition: {
    name: "take_screenshot",
    description:
      "Take a screenshot of the screen (macOS). Returns the file path — " +
      "use send_file to deliver it to the user. " +
      "Use this when the user asks for a visual capture, proof, or screenshot of something on screen. " +
      "Combine with run_shell_command to open a URL first, then screenshot it.",
    parameters: {
      type: "object",
      properties: {
        filename: {
          type: "string",
          description:
            'Output filename (without path). Default: "screenshot.png"',
        },
      },
      required: [],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filename = (args["filename"] as string) || "screenshot.png";
    const cwd = context.cwd || process.cwd();
    const outDir = resolve(cwd, "screenshots");

    if (!existsSync(outDir)) {
      mkdirSync(outDir, { recursive: true });
    }

    const outPath = join(outDir, filename);

    try {
      // macOS screencapture
      await execAsync(`screencapture -x "${outPath}"`, { timeout: 10_000 });

      if (!existsSync(outPath)) {
        return "Screenshot failed — file was not created. This tool requires macOS with screencapture.";
      }

      return `Screenshot saved to: ${outPath}\nUse send_file to deliver it to the user.`;
    } catch (error: any) {
      return `Screenshot failed: ${error.message}`;
    }
  },
};
