/**
 * build_tool — On-demand Python tool creation for the ReAct loop.
 *
 * Lets the owl write Python code for a new capability, install its pip deps,
 * save it to the synthesized directory, and hot-register it in the live
 * ToolRegistry — all in a single tool call during a conversation.
 *
 * Attach context with attachBuildTool() after registry + ledger are created.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { join } from "node:path";
import type { ToolImplementation, ToolContext, ToolRegistry } from "./registry.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import { PythonAnalyzer } from "../evolution/python-analyzer.js";
import { PythonAdapter } from "../evolution/python-adapter.js";
import { log } from "../logger.js";

const execFileAsync = promisify(execFile);

// ─── Context refs (attached from src/index.ts) ───────────────────

let registryRef: ToolRegistry | null = null;
let ledgerRef: CapabilityLedger | null = null;
let synthesizedDirRef: string | null = null;

export function attachBuildTool(
  registry: ToolRegistry,
  ledger: CapabilityLedger,
  synthesizedDir: string,
): void {
  registryRef = registry;
  ledgerRef = ledger;
  synthesizedDirRef = synthesizedDir;
  log.tool.debug("build-tool.attach: context attached", { synthesizedDir });
}

// ─── Boilerplate injected if the owl omits the __main__ guard ────

const MAIN_GUARD = `
if __name__ == "__main__":
    import sys as _sys
    _args = __import__("json").loads(_sys.argv[1]) if len(_sys.argv) > 1 else {}
    _cwd  = _sys.argv[2] if len(_sys.argv) > 2 else "."
    print(execute(_args, _cwd))
`;

function ensureMainGuard(code: string): string {
  return code.includes('__name__ == "__main__"') ? code : code + MAIN_GUARD;
}

// ─── Tool definition ─────────────────────────────────────────────

export const BuildToolTool: ToolImplementation = {
  definition: {
    name: "build_tool",
    description:
      "Create and immediately register a new Python tool in your tool registry. " +
      "Write Python code that implements a new capability; it will be saved, validated, and hot-loaded so you can use it in the same turn. " +
      "Use this whenever the user asks you to build, create, or synthesize a new tool. " +
      "The Python code must: (1) start with '# TOOL_NAME: snake_case_name' and '# DESCRIPTION: what it does' comment headers; " +
      "(2) define an execute(args: dict, cwd: str) -> str function that returns a JSON string; " +
      "(3) import json. " +
      "Example tool skeleton:\n" +
      "  # TOOL_NAME: instagram_downloader\n" +
      "  # DESCRIPTION: Download Instagram reels using yt-dlp\n" +
      "  import json, subprocess\n" +
      "  def execute(args, cwd):\n" +
      "      url = args.get('url', '')\n" +
      "      ...\n" +
      "      return json.dumps({'success': True, 'path': '/tmp/video.mp4'})",
    parameters: {
      type: "object",
      properties: {
        toolName: {
          type: "string",
          description: "Snake_case name (e.g. instagram_downloader). Used as the tool's filename and registry key.",
        },
        description: {
          type: "string",
          description: "One-line description of what this tool does.",
        },
        pythonCode: {
          type: "string",
          description: "Complete Python code. Must include # TOOL_NAME and # DESCRIPTION headers and an execute(args, cwd) function.",
        },
        dependencies: {
          type: "array",
          items: { type: "string" },
          description: "pip packages to install before loading (e.g. ['yt-dlp', 'instaloader']). Installed on the host via pip3.",
        },
      },
      required: ["toolName", "description", "pythonCode"],
    },
    capabilities: ["tool-creation", "synthesis", "meta"],
    executionPolicy: { timeoutMs: 120_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const start = Date.now();

    // ── 1. Entry ──────────────────────────────────────────────────
    const toolName = (args["toolName"] as string | undefined)?.trim() ?? "";
    const description = (args["description"] as string | undefined)?.trim() ?? "";
    const rawCode = (args["pythonCode"] as string | undefined) ?? "";
    const dependencies = Array.isArray(args["dependencies"])
      ? (args["dependencies"] as string[])
      : [];
    const depsInstalled: string[] = [];
    const depsSkipped: string[] = [];

    log.tool.debug("build-tool.execute: entry", { toolName, description, codeLen: rawCode.length, dependencies });

    // ── 2. Guard: context attached? ───────────────────────────────
    if (!registryRef || !ledgerRef || !synthesizedDirRef) {
      log.tool.error("build-tool.execute: context not attached", new Error("NOT_READY"), {});
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "build_tool context not initialized" } });
    }

    if (!toolName) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "toolName is required" } });
    }
    if (!rawCode) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "pythonCode is required" } });
    }

    // ── 3. Safety analysis ────────────────────────────────────────
    log.tool.debug("build-tool.execute: running safety analysis", { toolName });
    const analysis = PythonAnalyzer.analyze(rawCode);
    if (!analysis.safe) {
      log.tool.warn("build-tool.execute: safety violations detected", { toolName, patterns: analysis.patterns });
      return JSON.stringify({
        success: false,
        error: {
          code: "SAFETY_VIOLATION",
          message: `Code contains forbidden patterns: ${analysis.patterns.join(", ")}. Rewrite without those patterns.`,
          violations: analysis.patterns,
        },
      });
    }
    log.tool.debug("build-tool.execute: safety check passed", { toolName });

    // ── 4. Install pip dependencies (host shell, per-package with pre-check) ──
    if (dependencies.length > 0) {
      log.tool.debug("build-tool.execute: processing dependencies", { toolName, dependencies });
      const installed = depsInstalled;
      const skipped = depsSkipped;

      for (const pkg of dependencies) {
        // Python module name: yt-dlp → yt_dlp, Pillow → PIL, etc.
        const importName = pkg.replace(/-/g, "_").split("==")[0]!;

        // Pre-check: if already importable, skip pip entirely
        log.tool.debug("build-tool.execute: pre-checking package", { toolName, pkg, importName });
        const alreadyInstalled = await execFileAsync(
          "python3",
          ["-c", `import ${importName}`],
          { timeout: 5_000 },
        ).then(() => true).catch(() => false);

        if (alreadyInstalled) {
          log.tool.debug("build-tool.execute: package already importable, skipping install", { toolName, pkg });
          skipped.push(pkg);
          continue;
        }

        // Install: --user avoids permission issues, SIGKILL ensures timeout is respected
        log.tool.debug("build-tool.execute: installing package", { toolName, pkg });
        try {
          const { stderr } = await execFileAsync(
            "pip3",
            ["install", "--user", "--quiet", pkg],
            { timeout: 90_000, killSignal: "SIGKILL" },
          );
          if (stderr) log.tool.warn("build-tool.execute: pip stderr", { toolName, pkg, stderr: stderr.slice(0, 300) });
          installed.push(pkg);
          log.tool.debug("build-tool.execute: package installed", { toolName, pkg });
        } catch (err) {
          log.tool.error("build-tool.execute: pip install failed", err as Error, { toolName, pkg });
          return JSON.stringify({
            success: false,
            error: { code: "INSTALL_FAILED", message: `pip3 install ${pkg} failed: ${(err as Error).message}` },
          });
        }
      }

      log.tool.debug("build-tool.execute: dependency step complete", { toolName, installed, skipped });
    } else {
      log.tool.debug("build-tool.execute: no dependencies to install", { toolName });
    }

    // ── 5. Normalize code — inject __main__ guard if missing ──────
    const pythonCode = ensureMainGuard(rawCode);
    const fileName = `${toolName}.py`;
    const filePath = join(synthesizedDirRef, fileName);

    log.tool.debug("build-tool.execute: saving tool file", { toolName, filePath });

    // ── 6. Ensure synthesized dir exists and write file ───────────
    try {
      await mkdir(synthesizedDirRef, { recursive: true });
      await writeFile(filePath, pythonCode, "utf-8");
      log.tool.debug("build-tool.execute: file written", { toolName, filePath, bytes: pythonCode.length });
    } catch (err) {
      log.tool.error("build-tool.execute: file write failed", err as Error, { toolName, filePath });
      return JSON.stringify({
        success: false,
        error: { code: "WRITE_FAILED", message: `Failed to write tool file: ${(err as Error).message}` },
      });
    }

    // ── 7. Wrap with PythonAdapter and register in registry ───────
    log.tool.debug("build-tool.execute: wrapping with PythonAdapter", { toolName, filePath });
    let toolImpl: ToolImplementation;
    try {
      toolImpl = PythonAdapter.wrap(filePath, pythonCode);
    } catch (err) {
      log.tool.error("build-tool.execute: PythonAdapter.wrap failed", err as Error, { toolName });
      return JSON.stringify({
        success: false,
        error: { code: "WRAP_FAILED", message: `Failed to wrap Python tool: ${(err as Error).message}` },
      });
    }

    try {
      registryRef.register(toolImpl);
      log.tool.debug("build-tool.execute: tool registered in registry", { toolName: toolImpl.definition.name });
    } catch (err) {
      log.tool.error("build-tool.execute: registry.register failed", err as Error, { toolName });
      return JSON.stringify({
        success: false,
        error: { code: "REGISTER_FAILED", message: `Failed to register tool: ${(err as Error).message}` },
      });
    }

    // ── 8. Persist to ledger ──────────────────────────────────────
    log.tool.debug("build-tool.execute: persisting to ledger", { toolName });
    try {
      await ledgerRef.recordPython({
        toolName: toolImpl.definition.name,
        fileName,
        description,
        createdBy: "build_tool",
        rationale: `User-requested on-demand tool creation via build_tool`,
        dependencies,
        safetyNote: "Passed PythonAnalyzer safety check",
      });
      log.tool.debug("build-tool.execute: ledger updated", { toolName });
    } catch (err) {
      // Non-fatal: tool is already registered and usable. Log and continue.
      log.tool.warn("build-tool.execute: ledger persist failed (non-fatal)", { toolName, err: (err as Error).message });
    }

    // ── 9. Exit ───────────────────────────────────────────────────
    const durationMs = Date.now() - start;
    const registeredName = toolImpl.definition.name;
    log.tool.debug("build-tool.execute: exit", { toolName, registeredName, durationMs, success: true });

    return JSON.stringify({
      success: true,
      data: {
        registeredName,
        filePath,
        message: `Tool "${registeredName}" built, registered, and ready to use. Call it now.`,
        depsInstalled: depsInstalled.length > 0 ? depsInstalled : undefined,
        depsSkipped: depsSkipped.length > 0 ? depsSkipped : undefined,
        durationMs,
      },
    });
  },
};
