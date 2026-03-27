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
    const rawFilename = (args["filename"] as string) || "screenshot.png";

    const safeChars = /^[a-zA-Z0-9_\-. ]+$/;
    if (!safeChars.test(rawFilename)) {
      return `Screenshot failed: filename contains invalid characters. Use only alphanumeric, dashes, underscores, spaces, and dots.`;
    }

    if (rawFilename.length > 255) {
      return `Screenshot failed: filename too long (max 255 characters).`;
    }

    const filename = rawFilename;
    const cwd = context.cwd || process.cwd();
    const outDir = resolve(cwd, "screenshots");

    if (!existsSync(outDir)) {
      mkdirSync(outDir, { recursive: true });
    }

    const outPath = join(outDir, filename);

    const resolvedPath = resolve(outPath);
    const resolvedDir = resolve(outDir);
    if (
      !resolvedPath.startsWith(
        resolvedDir + (process.platform === "win32" ? "\\" : "/"),
      )
    ) {
      return `Screenshot failed: path traversal detected.`;
    }

    if (process.platform !== "darwin") {
      const hint =
        process.platform === "linux"
          ? " On Linux/Docker, screenshot requires a display server (X11) which is not available in containerized environments."
          : " This tool requires macOS.";
      return `Screenshot failed: not supported on ${process.platform}.${hint}`;
    }

    try {
      await execAsync(`screencapture -x "${outPath}"`, { timeout: 10_000 });

      if (!existsSync(outPath)) {
        return "Screenshot failed — file was not created. Ensure macOS screencapture is available and has screen recording permissions.";
      }

      return `Screenshot saved to: ${outPath}\nUse send_file to deliver it to the user.`;
    } catch (error: any) {
      if (error.message.includes("screencapture: cannot hook")) {
        return "Screenshot failed: screen recording permission not granted. Go to System Preferences > Security & Privacy > Privacy > Screen Recording and enable terminal/script runner access.";
      }
      return `Screenshot failed: ${error.message}`;
    }
  },
};
