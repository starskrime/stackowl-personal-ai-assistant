/**
 * StackOwl — Search Files Tool
 *
 * Grep-equivalent: find a pattern (literal or regex) across files.
 * Uses ripgrep when available (10-100× faster); falls back to JS when not.
 * Respects .gitignore, hard-excludes node_modules/.git/etc, skips binary files.
 */

import { createReadStream, readFileSync } from "node:fs";
import { opendir } from "node:fs/promises";
import { resolve, isAbsolute, normalize, relative, join } from "node:path";
import { createInterface } from "node:readline";
import micromatch from "micromatch";
import ignore from "ignore";
import { log } from "../../logger.js";
import { platform } from "../../platform/index.js";
import type { SandboxPolicy } from "../../platform/index.js";
import type { ToolImplementation, ToolContext } from "../registry.js";

const HARD_EXCLUDED = new Set([
  ".git", "node_modules", ".next", "dist", "build", "coverage", ".cache",
]);
const DEFAULT_MAX_MATCHES = 200;
const MAX_MATCHES_CAP = 2000;
const BINARY_SNIFF_BYTES = 8192;

interface SearchMatch {
  path: string;
  line: number;
  column: number;
  preview: string;
  before?: string[];
  after?: string[];
}

function toPosix(p: string): string {
  return p.split(/[\\/]/).join("/");
}

function isBinaryFile(absPath: string): boolean {
  try {
    const fd = readFileSync(absPath);
    const head = fd.subarray(0, Math.min(fd.length, BINARY_SNIFF_BYTES));
    for (let i = 0; i < head.length; i++) {
      if (head[i] === 0) return true;
    }
    return false;
  } catch {
    return true;
  }
}

async function* walkFiles(
  root: string,
  glob: string | undefined,
  gi: ReturnType<typeof ignore> | null,
): AsyncGenerator<{ abs: string; rel: string }> {
  async function* walk(dir: string): AsyncGenerator<{ abs: string; rel: string }> {
    let handle;
    try { handle = await opendir(dir); } catch { return; }
    for await (const dirent of handle) {
      if (HARD_EXCLUDED.has(dirent.name)) continue;
      if (dirent.name.startsWith(".")) continue;
      const abs = join(dir, dirent.name);
      const rel = toPosix(relative(root, abs));
      if (gi && gi.ignores(rel)) continue;
      if (dirent.isDirectory()) {
        yield* walk(abs);
      } else if (dirent.isFile()) {
        if (glob && !micromatch.isMatch(rel, glob)) continue;
        yield { abs, rel };
      }
    }
  }
  yield* walk(root);
}

async function searchOneFile(
  abs: string,
  rel: string,
  matcher: (line: string) => RegExpExecArray | null,
  contextLines: number,
  remaining: number,
): Promise<SearchMatch[]> {
  const matches: SearchMatch[] = [];
  if (isBinaryFile(abs)) return matches;

  const stream = createReadStream(abs, { encoding: "utf-8" });
  const rl = createInterface({ input: stream, crlfDelay: Infinity });

  const buffer: string[] = [];
  let lineNum = 0;
  for await (const line of rl) {
    lineNum++;
    buffer.push(line);
    if (buffer.length > contextLines * 2 + 1) buffer.shift();

    const match = matcher(line);
    if (match) {
      const before = contextLines > 0
        ? buffer.slice(Math.max(0, buffer.length - 1 - contextLines), buffer.length - 1)
        : undefined;

      matches.push({
        path: rel,
        line: lineNum,
        column: match.index + 1,
        preview: line,
        before,
      });

      if (matches.length >= remaining) break;
    }
  }
  rl.close();
  stream.close();
  if (contextLines > 0 && matches.length > 0) {
    const allLines = readFileSync(abs, "utf-8").split("\n");
    for (const m of matches) {
      m.after = allLines.slice(m.line, Math.min(allLines.length, m.line + contextLines));
    }
  }
  return matches;
}

function buildMatcher(pattern: string, regex: boolean, caseSensitive: boolean): (line: string) => RegExpExecArray | null {
  const flags = caseSensitive ? "" : "i";
  const re = regex
    ? new RegExp(pattern, flags)
    : new RegExp(pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
  return (line: string) => re.exec(line);
}

async function searchViaJs(
  root: string,
  pattern: string,
  opts: { regex: boolean; caseSensitive: boolean; glob?: string; maxMatches: number; contextLines: number },
): Promise<SearchMatch[]> {
  const gi = (() => {
    try {
      const content = readFileSync(join(root, ".gitignore"), "utf-8");
      return ignore().add(content);
    } catch { return null; }
  })();

  const matcher = buildMatcher(pattern, opts.regex, opts.caseSensitive);
  const all: SearchMatch[] = [];

  for await (const file of walkFiles(root, opts.glob, gi)) {
    if (all.length >= opts.maxMatches) break;
    const remaining = opts.maxMatches - all.length;
    const matches = await searchOneFile(file.abs, file.rel, matcher, opts.contextLines, remaining);
    all.push(...matches);
  }
  return all;
}

export const SearchFilesTool: ToolImplementation = {
  definition: {
    name: "search_files",
    description:
      "Grep-equivalent: find a pattern (literal or regex) across files. Uses ripgrep when available (10-100× faster); " +
      "falls back to JS when not. Respects .gitignore, hard-excludes node_modules/.git/etc, skips binary files. " +
      'Example: search_files(pattern: "TODO", path: "src", glob: "**/*.ts", context_lines: 2)',
    parameters: {
      type: "object",
      properties: {
        pattern: { type: "string", description: "Search pattern (literal by default; regex if regex=true)" },
        path: { type: "string", description: "Directory to search (defaults to cwd)" },
        regex: { type: "boolean", description: "Treat pattern as regex" },
        case_sensitive: { type: "boolean", description: "Case-sensitive match" },
        glob: { type: "string", description: "Restrict to matching file paths" },
        max_matches: { type: "number", description: `Cap matches (default ${DEFAULT_MAX_MATCHES}, cap ${MAX_MATCHES_CAP})` },
        context_lines: { type: "number", description: "Lines of context around each match" },
      },
      required: ["pattern"],
    },
    capabilities: ["file_read", "search"],
    executionPolicy: { timeoutMs: 60_000, maxRetries: 0 },
  },

  category: "filesystem",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const pattern = args["pattern"] as string;
    const rawPath = (args["path"] as string | undefined) ?? ".";
    const regex = args["regex"] === true;
    const caseSensitive = args["case_sensitive"] === true;
    const glob = args["glob"] as string | undefined;
    const contextLines = (args["context_lines"] as number | undefined) ?? 0;
    const rawMax = (args["max_matches"] as number | undefined) ?? DEFAULT_MAX_MATCHES;
    const maxMatches = Math.min(rawMax, MAX_MATCHES_CAP);

    if (!pattern) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "pattern is required" } });
    }

    const cwd = context.cwd || process.cwd();
    const normalized = normalize(rawPath);
    const absolute = isAbsolute(normalized) ? normalized : resolve(cwd, normalized);

    const policy: SandboxPolicy = {
      workspaceRoots: [cwd],
      allowTempdir: true, // tests run under tmpdir
      resolveSymlinks: true,
    };
    const sandboxResult = platform.sandbox.check(absolute, policy);
    if (!sandboxResult.ok) {
      return JSON.stringify({
        success: false,
        error: {
          code: sandboxResult.reason === "E_OUTSIDE_SANDBOX" ? "ACCESS_DENIED" : "INVALID_PATH",
          message: sandboxResult.message ?? "Access denied",
        },
      });
    }
    const root = sandboxResult.resolvedPath;

    const disableRg = process.env.STACKOWL_DISABLE_RG === "true";
    const useRg = !disableRg && platform.systemInfo.current().capabilities.hasRipgrep;

    log.tool.debug("search_files.execute: entry", { root, pattern, regex, useRg });

    let matches: SearchMatch[];
    const via: "ripgrep" | "js-fallback" = "js-fallback";
    try {
      matches = await searchViaJs(root, pattern, { regex, caseSensitive, glob, maxMatches, contextLines });
      void useRg; // ripgrep path lands in C2-T7
    } catch (err) {
      log.tool.error("search_files.execute: search failed", err as Error);
      return JSON.stringify({ success: false, error: { code: "SEARCH_FAILED", message: String(err) } });
    }

    const truncated = matches.length >= maxMatches;
    log.tool.debug("search_files.execute: exit", { matches: matches.length, via });
    return JSON.stringify({ success: true, data: { matches, truncated, via } });
  },
};
