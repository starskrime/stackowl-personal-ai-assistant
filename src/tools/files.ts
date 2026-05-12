/**
 * StackOwl — File Tools
 *
 * Allows owls to read and write files directly in the workspace.
 * Sandboxed to workspace/cwd root. Includes surgical edit tool.
 */

import { readFile, writeFile } from "node:fs/promises";
import { resolve, isAbsolute, normalize } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { platform } from "../platform/index.js";
import type { SandboxPolicy } from "../platform/index.js";

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

    // Sandbox check: resolve symlinks and validate boundaries
    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: true, // file tools allow temp for build artifacts
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(resolved, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("read_file.execute: sandbox check failed", {
        path: filePath,
        reason: sandboxResult.reason,
        message: sandboxResult.message,
      });
      return `Access denied: ${sandboxResult.message ?? "outside workspace"}`;
    }
    const safePath = sandboxResult.resolvedPath;

    // 1. ENTRY
    log.tool.debug("read_file.execute: entry", { op: "read", path: safePath });
    // 2. DECISION
    log.tool.debug("read_file.execute: operation branch", { chosen: "read", path: safePath });

    try {
      // 3. STEP — fs read
      log.tool.debug("read_file.execute: reading", { path: safePath });
      const content = await readFile(safePath, "utf-8");
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
      log.tool.error("read_file.execute: read failed", error, { path: safePath });
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

    // Sandbox check: resolve symlinks and validate boundaries
    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: true, // file tools allow temp for build artifacts
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(resolved, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("write_file.execute: sandbox check failed", {
        path: filePath,
        reason: sandboxResult.reason,
        message: sandboxResult.message,
      });
      return `Access denied: ${sandboxResult.message ?? "outside workspace"}`;
    }
    const safePath = sandboxResult.resolvedPath;

    // 1. ENTRY
    log.tool.debug("write_file.execute: entry", { op: "write", path: safePath, contentLen: content.length });
    // 2. DECISION
    log.tool.debug("write_file.execute: operation branch", { chosen: "write", path: safePath });

    try {
      // 3. STEP — fs write
      log.tool.debug("write_file.execute: writing", { path: safePath, bytes: content.length });
      await writeFile(safePath, content, "utf-8");
      log.tool.debug("write_file.execute: fs write complete", { bytes: content.length });

      const result = `Successfully wrote ${content.length} chars to ${filePath}`;
      // 4. EXIT
      log.tool.debug("write_file.execute: exit", { op: "write", resultLen: result.length });
      return result;
    } catch (error: any) {
      // ERROR
      log.tool.error("write_file.execute: write failed", error, { path: safePath });
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
      "Default: replaces only the FIRST occurrence. Set replace_all:true to replace every occurrence in one call.",
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
        replace_all: {
          type: "boolean",
          description: "If true, replaces every occurrence of old_string. If false or omitted, replaces only the first.",
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
    const replaceAll = args["replace_all"] === true;

    if (!filePath) throw new Error("Path argument missing");
    if (oldString === undefined) throw new Error("old_string argument missing");
    if (newString === undefined) throw new Error("new_string argument missing");

    const cwd = context.cwd || process.cwd();
    const normalizedInput = normalize(filePath);
    const resolved = isAbsolute(normalizedInput)
      ? normalizedInput
      : resolve(cwd, normalizedInput);

    // Sandbox check: resolve symlinks and validate boundaries
    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: true, // file tools allow temp for build artifacts
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(resolved, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("edit_file.execute: sandbox check failed", {
        path: filePath,
        reason: sandboxResult.reason,
        message: sandboxResult.message,
      });
      return `Access denied: ${sandboxResult.message ?? "outside workspace"}`;
    }
    const safePath = sandboxResult.resolvedPath;

    if (replaceAll && oldString === "") {
      return `Error: old_string cannot be empty when replace_all=true (would loop forever).`;
    }

    // 1. ENTRY
    log.tool.debug("edit_file.execute: entry", { op: "edit", path: safePath, oldLen: oldString.length, newLen: newString.length, replaceAll });
    // 2. DECISION
    log.tool.debug("edit_file.execute: operation branch", { chosen: replaceAll ? "replace-all" : "surgical-edit", path: safePath });

    try {
      const content = await readFile(safePath, "utf-8");

      if (replaceAll) {
        if (oldString === newString) {
          log.tool.debug("edit_file.execute: exit", { op: "edit", noop: true });
          return `0 replacements (no-op: replacement equals search) in ${filePath}`;
        }
        const parts = content.split(oldString);
        const count = parts.length - 1;
        if (count === 0) {
          return `Error: old_string not found in ${filePath}. Make sure it matches exactly (including whitespace and newlines).`;
        }
        const updated = parts.join(newString);
        await writeFile(safePath, updated, "utf-8");
        const preview = oldString.length > 40 ? oldString.slice(0, 40) + "…" : oldString;
        const result = `Replaced ${count} occurrences of '${preview}' in ${filePath}`;
        log.tool.debug("edit_file.execute: exit", { op: "edit", count });
        return result;
      }

      // single-occurrence branch (existing behaviour)
      const idx = content.indexOf(oldString);
      if (idx === -1) {
        return `Error: old_string not found in ${filePath}. Make sure it matches exactly (including whitespace and newlines).`;
      }
      const updated = content.slice(0, idx) + newString + content.slice(idx + oldString.length);
      await writeFile(safePath, updated, "utf-8");

      const lineNum = content.slice(0, idx).split("\n").length;
      const result = `Successfully edited ${filePath} at line ~${lineNum} (replaced ${oldString.length} chars with ${newString.length} chars)`;
      // 4. EXIT
      log.tool.debug("edit_file.execute: exit", { op: "edit", resultLen: result.length });
      return result;
    } catch (error: any) {
      // ERROR
      log.tool.error("edit_file.execute: edit failed", error, { path: safePath });
      return `Failed to edit file: ${error.message}`;
    }
  },
};
