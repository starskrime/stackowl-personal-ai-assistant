/**
 * StackOwl — List Directory Tool
 *
 * Sandboxed, glob-aware directory listing with gitignore support.
 * Hard-excludes node_modules/.git/etc. Hidden dotfiles excluded unless include_hidden:true.
 */

import { opendir, stat, readFile } from "node:fs/promises";
import { resolve, isAbsolute, normalize, relative, join } from "node:path";
import micromatch from "micromatch";
import ignore from "ignore";
import { log } from "../../logger.js";
import { platform } from "../../platform/index.js";
import type { SandboxPolicy } from "../../platform/index.js";
import type { ToolImplementation, ToolContext } from "../registry.js";

const HARD_EXCLUDED = new Set([
  ".git", "node_modules", ".next", "dist", "build", "coverage", ".cache",
]);

const MAX_RESULTS_HARD_CAP = 5000;
const DEFAULT_MAX_RESULTS = 500;

interface ListEntry {
  path: string;
  type: "file" | "dir" | "symlink";
  size?: number;
  modified?: string;
}

async function loadGitignore(root: string): Promise<ReturnType<typeof ignore> | null> {
  try {
    const content = await readFile(join(root, ".gitignore"), "utf-8");
    return ignore().add(content);
  } catch {
    return null;
  }
}

function toPosix(p: string): string {
  return p.split(/[\\/]/).join("/");
}

export const ListDirectoryTool: ToolImplementation = {
  definition: {
    name: "list_directory",
    description:
      "List files and directories. Set `recursive: true` or pass a `glob` (e.g. \"**/*.ts\") to descend. " +
      "Respects .gitignore by default; hard-excludes node_modules/.git/etc. Hidden dotfiles excluded unless include_hidden:true. " +
      'Example: list_directory(path: "src", recursive: true, glob: "**/*.ts")',
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "Workspace-relative or absolute path to list" },
        recursive: { type: "boolean", description: "Descend into subdirectories" },
        glob: { type: "string", description: "Optional glob like \"**/*.ts\" (implies recursive=true)" },
        include_hidden: { type: "boolean", description: "Include dotfiles" },
        respect_gitignore: { type: "boolean", description: "Honor .gitignore (default true)" },
        max_results: { type: "number", description: `Cap results (default ${DEFAULT_MAX_RESULTS}, hard cap ${MAX_RESULTS_HARD_CAP})` },
      },
      required: ["path"],
    },
    capabilities: ["file_read", "directory_list"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  category: "filesystem",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const rawPath = args["path"] as string;
    const recursive = args["recursive"] === true || !!args["glob"];
    const glob = args["glob"] as string | undefined;
    const includeHidden = args["include_hidden"] === true;
    const respectGitignore = args["respect_gitignore"] !== false;
    const rawMax = (args["max_results"] as number | undefined) ?? DEFAULT_MAX_RESULTS;
    const maxResults = Math.min(rawMax, MAX_RESULTS_HARD_CAP);

    if (!rawPath) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "path is required" } });
    }

    const cwd = context.cwd || process.cwd();
    const normalized = normalize(rawPath);
    const absolute = isAbsolute(normalized) ? normalized : resolve(cwd, normalized);

    log.tool.debug("list_directory.execute: entry", { path: absolute, recursive, glob, includeHidden });

    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: true, // tests create workspaces under tmpdir
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(absolute, policy);
    if (!sandboxResult.ok) {
      log.tool.warn("list_directory.execute: sandbox check failed", { reason: sandboxResult.reason });
      return JSON.stringify({
        success: false,
        error: {
          code: sandboxResult.reason === "E_OUTSIDE_SANDBOX" ? "ACCESS_DENIED" : "INVALID_PATH",
          message: sandboxResult.message ?? "Access denied",
        },
      });
    }
    const root = sandboxResult.resolvedPath;

    const gi = respectGitignore ? await loadGitignore(cwd) : null;
    const entries: ListEntry[] = [];
    let totalScanned = 0;
    let truncated = false;

    async function walk(dir: string): Promise<void> {
      if (entries.length >= maxResults) {
        truncated = true;
        return;
      }
      let handle;
      try {
        handle = await opendir(dir);
      } catch (err) {
        log.tool.warn("list_directory.walk: opendir failed", { dir, err: String(err) });
        return;
      }

      for await (const dirent of handle) {
        if (entries.length >= maxResults) {
          truncated = true;
          break;
        }
        totalScanned++;

        const name = dirent.name;
        if (HARD_EXCLUDED.has(name)) continue;
        if (!includeHidden && name.startsWith(".")) continue;

        const abs = join(dir, name);
        const rel = toPosix(relative(root, abs));

        if (gi && gi.ignores(rel)) continue;

        let type: ListEntry["type"];
        if (dirent.isSymbolicLink()) {
          type = "symlink";
          const symCheck = platform.sandbox.check(abs, policy);
          if (!symCheck.ok) continue;
        } else if (dirent.isDirectory()) {
          type = "dir";
        } else if (dirent.isFile()) {
          type = "file";
        } else {
          continue;
        }

        let size: number | undefined;
        let modified: string | undefined;
        if (type === "file") {
          try {
            const st = await stat(abs);
            size = st.size;
            modified = st.mtime.toISOString();
          } catch { /* skip */ }
        }

        if (glob && type === "file" && !micromatch.isMatch(rel, glob)) continue;

        entries.push({ path: rel, type, size, modified });

        if (recursive && type === "dir") {
          await walk(abs);
        }
      }
    }

    try {
      const rootStat = await stat(root);
      if (!rootStat.isDirectory()) {
        return JSON.stringify({ success: false, error: { code: "NOT_A_DIRECTORY", message: `${root} is not a directory` } });
      }
      await walk(root);
    } catch (err) {
      return JSON.stringify({ success: false, error: { code: "STAT_FAILED", message: String(err) } });
    }

    log.tool.debug("list_directory.execute: exit", { count: entries.length, truncated, totalScanned });
    return JSON.stringify({ success: true, data: { entries, truncated, totalScanned } });
  },
};
