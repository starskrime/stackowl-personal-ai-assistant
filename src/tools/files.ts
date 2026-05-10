/**
 * StackOwl — File Tools
 *
 * Allows owls to read and write files directly in the workspace.
 * Sandboxed to workspace/cwd root. Includes surgical edit tool.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve, isAbsolute, sep, normalize } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

// ─── Sandbox Helper ───────────────────────────────────────────────

function assertWithinSandbox(resolvedPath: string, cwd: string): void {
  // Check if running in Docker
  const inDocker =
    process.env.IN_DOCKER === "true" || existsSync("/.dockerenv");

  // In Docker, allow access to the entire container (it's already sandboxed)
  if (inDocker) {
    return; // Full access in Docker
  }

  // On host machine, restrict to workspace and /tmp
  const sandboxRoot = resolve(cwd);
  const isInWorkspace =
    resolvedPath.startsWith(sandboxRoot + sep) || resolvedPath === sandboxRoot;
  const isInTemp = resolvedPath.startsWith("/tmp/") || resolvedPath === "/tmp";

  if (!isInWorkspace && !isInTemp) {
    throw new Error(
      `Access denied: "${resolvedPath}" is outside the allowed paths. Allowed: ${sandboxRoot}, /tmp (or entire container in Docker)`,
    );
  }
}

// ─── Read File ────────────────────────────────────────────────────

export const ReadFileTool: ToolImplementation = {
  definition: {
    name: "read_file",
    description:
      "Read a file's contents with line numbers. Truncates at 20KB. " +
      "Use for inspecting workspace files, configs, logs, and code. " +
      "For web pages, use web_fetch instead.",
    parameters: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description:
            "Path to the file to read (relative to workspace or absolute)",
        },
      },
      required: ["path"],
    },
    capabilities: ["file_read"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filePath = args["path"] as string;
    if (!filePath) throw new Error("Path argument missing");

    const cwd = context.cwd || process.cwd();
    const normalizedInput = normalize(filePath);
    const resolved = isAbsolute(normalizedInput)
      ? normalizedInput
      : resolve(cwd, normalizedInput);
    assertWithinSandbox(resolved, cwd);

    // 1. ENTRY
    log.tool.debug("read_file.execute: entry", { op: "read", path: resolved });
    // 2. DECISION
    log.tool.debug("read_file.execute: operation branch", { chosen: "read", path: resolved });

    try {
      // 3. STEP — fs read
      log.tool.debug("read_file.execute: reading", { path: resolved });
      const content = await readFile(resolved, "utf-8");
      const truncated =
        content.length > 20000 ? content.substring(0, 20000) : content;
      const wasTruncated = content.length > 20000;
      log.tool.debug("read_file.execute: fs read complete", { bytes: content.length, truncated: wasTruncated });

      const lines = truncated.split("\n");
      const numbered = lines
        .map((line, i) => `${String(i + 1).padStart(4, " ")} | ${line}`)
        .join("\n");

      const result = wasTruncated
        ? `[File truncated at 20000 chars — showing first ${lines.length} lines]\n\n${numbered}\n...[truncated]`
        : numbered;
      // 4. EXIT
      log.tool.debug("read_file.execute: exit", { op: "read", resultLen: result.length });
      return result;
    } catch (error: any) {
      // ERROR
      log.tool.error("read_file.execute: read failed", error, { path: resolved });
      return `Failed to read file: ${error.message}`;
    }
  },
};

// ─── Write File ───────────────────────────────────────────────────

export const WriteFileTool: ToolImplementation = {
  definition: {
    name: "write_file",
    description:
      "Write string content to a file (creates or overwrites). Use edit_file for surgical changes.",
    parameters: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description:
            "Path to the file to write (relative to workspace or absolute)",
        },
        content: {
          type: "string",
          description: "The string content to write",
        },
      },
      required: ["path", "content"],
    },
    sequential: true,
    capabilities: ["file_write"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filePath = args["path"] as string;
    const content = args["content"] as string;

    if (!filePath) throw new Error("Path argument missing");
    if (content === undefined) throw new Error("Content argument missing");

    const cwd = context.cwd || process.cwd();
    const normalizedInput = normalize(filePath);
    const resolved = isAbsolute(normalizedInput)
      ? normalizedInput
      : resolve(cwd, normalizedInput);
    assertWithinSandbox(resolved, cwd);

    // 1. ENTRY
    log.tool.debug("write_file.execute: entry", { op: "write", path: resolved, contentLen: content.length });
    // 2. DECISION
    log.tool.debug("write_file.execute: operation branch", { chosen: "write", path: resolved });

    try {
      // 3. STEP — fs write
      log.tool.debug("write_file.execute: writing", { path: resolved, bytes: content.length });
      await writeFile(resolved, content, "utf-8");
      log.tool.debug("write_file.execute: fs write complete", { bytes: content.length });

      const result = `Successfully wrote ${content.length} chars to ${filePath}`;
      // 4. EXIT
      log.tool.debug("write_file.execute: exit", { op: "write", resultLen: result.length });
      return result;
    } catch (error: any) {
      // ERROR
      log.tool.error("write_file.execute: write failed", error, { path: resolved });
      return `Failed to write file: ${error.message}`;
    }
  },
};

// ─── Edit File ────────────────────────────────────────────────────

export const EditFileTool: ToolImplementation = {
  definition: {
    name: "edit_file",
    description:
      "Make a surgical edit to a file by replacing an exact string. " +
      "Prefer this over write_file when changing only part of a file. " +
      "The old_string must match exactly (including whitespace). " +
      "Only replaces the FIRST occurrence.",
    parameters: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description:
            "Path to the file to edit (relative to workspace or absolute)",
        },
        old_string: {
          type: "string",
          description:
            "The exact string to find and replace (whitespace-sensitive)",
        },
        new_string: {
          type: "string",
          description: "The replacement string (use empty string to delete)",
        },
      },
      required: ["path", "old_string", "new_string"],
    },
    sequential: true,
    capabilities: ["file_write", "file_edit"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filePath = args["path"] as string;
    const oldString = args["old_string"] as string;
    const newString = args["new_string"] as string;

    if (!filePath) throw new Error("Path argument missing");
    if (oldString === undefined) throw new Error("old_string argument missing");
    if (newString === undefined) throw new Error("new_string argument missing");

    const cwd = context.cwd || process.cwd();
    const normalizedInput = normalize(filePath);
    const resolved = isAbsolute(normalizedInput)
      ? normalizedInput
      : resolve(cwd, normalizedInput);
    assertWithinSandbox(resolved, cwd);

    // 1. ENTRY
    log.tool.debug("edit_file.execute: entry", { op: "edit", path: resolved, oldLen: oldString.length, newLen: newString.length });
    // 2. DECISION
    log.tool.debug("edit_file.execute: operation branch", { chosen: "surgical-edit", path: resolved });

    try {
      // 3. STEP — read then write
      log.tool.debug("edit_file.execute: reading for edit", { path: resolved });
      const content = await readFile(resolved, "utf-8");
      const idx = content.indexOf(oldString);
      if (idx === -1) {
        return `Error: old_string not found in ${filePath}. Make sure it matches exactly (including whitespace and newlines).`;
      }

      const updated =
        content.slice(0, idx) +
        newString +
        content.slice(idx + oldString.length);
      log.tool.debug("edit_file.execute: writing edited file", { path: resolved, bytes: updated.length });
      await writeFile(resolved, updated, "utf-8");

      const lineNum = content.slice(0, idx).split("\n").length;
      const result = `Successfully edited ${filePath} at line ~${lineNum} (replaced ${oldString.length} chars with ${newString.length} chars)`;
      // 4. EXIT
      log.tool.debug("edit_file.execute: exit", { op: "edit", resultLen: result.length });
      return result;
    } catch (error: any) {
      // ERROR
      log.tool.error("edit_file.execute: edit failed", error, { path: resolved });
      return `Failed to edit file: ${error.message}`;
    }
  },
};
