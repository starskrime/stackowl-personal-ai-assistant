/**
 * StackOwl — Python Tool Adapter
 *
 * Wraps a synthesized Python tool file into a ToolImplementation so it can be
 * registered in the ToolRegistry and invoked by the ReAct engine exactly like
 * any built-in TypeScript tool.
 *
 * Execution model: the Python file is run in a child process via `python3`.
 * The child process receives:
 *   argv[1] — JSON-encoded args dict
 *   argv[2] — cwd string
 * and must print its result to stdout.
 *
 * Python tool file contract:
 *   # TOOL_NAME: synth_<snake_case_name>
 *   # DESCRIPTION: one-line description
 *   # PARAMETERS:
 *   #   <param>: <type> - <description>
 *   import json, sys
 *   def execute(args: dict, cwd: str) -> str: ...
 *   if __name__ == "__main__":
 *       args = json.loads(sys.argv[1])
 *       cwd = sys.argv[2]
 *       print(execute(args, cwd))
 */

import { execFile } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import { log } from "../logger.js";

/**
 * Parse the header comment block from a Python tool file.
 * Extracts TOOL_NAME and DESCRIPTION.
 */
function parseHeader(code: string): { name: string; description: string } {
  const lines = code.split("\n");
  let name = "synth_unknown";
  let description = "Synthesized Python tool";

  for (const line of lines) {
    const nameLine = line.match(/^#\s*TOOL_NAME:\s*(.+)/u);
    if (nameLine) {
      name = nameLine[1].trim();
      continue;
    }
    const descLine = line.match(/^#\s*DESCRIPTION:\s*(.+)/u);
    if (descLine) {
      description = descLine[1].trim();
      continue;
    }
  }
  return { name, description };
}

export class PythonAdapter {
  /**
   * Wrap a Python tool file into a ToolImplementation.
   *
   * @param filePath  Absolute path to the .py file on disk
   * @param code      Source code (used for header parsing only; the file on disk is executed)
   */
  static wrap(filePath: string, code: string): ToolImplementation {
    const { name, description } = parseHeader(code);
    log.synthesis.debug("python-adapter.wrap: parsed header", { name, description, filePath });

    return {
      definition: {
        name,
        description,
        parameters: {
          type: "object",
          properties: {},
          required: [],
        },
      },
      source: "synthesized",
      execute: async (args: Record<string, unknown>, context: ToolContext): Promise<string> => {
        log.synthesis.debug("python-adapter.execute: entry", { name, filePath });

        const argsJson = JSON.stringify(args);
        const cwd = context.cwd ?? process.cwd();

        return new Promise<string>((resolve) => {
          execFile(
            "python3",
            [filePath, argsJson, cwd],
            {
              timeout: 30_000,
              cwd,
              env: {
                PATH: process.env["PATH"],
                HOME: process.env["HOME"],
                PYTHONPATH: process.env["PYTHONPATH"],
              },
            },
            (err, stdout, stderr) => {
              if (err) {
                log.synthesis.error("python-adapter.execute: process error", err, { name, stderr });
                resolve(
                  `ERROR executing ${name}: ${err.message}${stderr ? `\nstderr: ${stderr}` : ""}`,
                );
                return;
              }
              if (stderr) {
                log.synthesis.warn("python-adapter.execute: stderr output", { name, stderr });
              }
              log.synthesis.debug("python-adapter.execute: exit", { name, resultLen: stdout.length });
              resolve(stdout);
            },
          );
        });
      },
    };
  }
}
