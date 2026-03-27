/**
 * StackOwl — Mermaid Diagram Tool
 *
 * Creates diagrams from Mermaid syntax, saving .mmd files and optionally
 * generating SVG via mermaid-cli.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { resolve, basename } from "node:path";
import { exec } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation, ToolContext } from "../registry.js";

const execAsync = promisify(exec);
const EXEC_TIMEOUT_MS = 30_000;

export const MermaidDiagramTool: ToolImplementation = {
  definition: {
    name: "mermaid_diagram",
    description:
      "Create diagrams from Mermaid syntax (flowcharts, sequence diagrams, etc). Saves .mmd file and attempts SVG generation.",
    parameters: {
      type: "object",
      properties: {
        code: {
          type: "string",
          description: "Mermaid diagram syntax string.",
        },
        output: {
          type: "string",
          description:
            "Optional output filename (without extension). Defaults to 'diagram-<timestamp>'.",
        },
      },
      required: ["code"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const code = args["code"] as string;
      if (!code) return "Error: 'code' parameter is required.";

      const name = (args["output"] as string) || `diagram-${Date.now()}`;
      const safeName = basename(name).replace(/[^a-zA-Z0-9_-]/g, "_");

      const diagramsDir = resolve(_context.cwd, "workspace", "diagrams");
      await mkdir(diagramsDir, { recursive: true });

      const mmdPath = resolve(diagramsDir, `${safeName}.mmd`);
      await writeFile(mmdPath, code, "utf-8");

      // Attempt SVG generation via mermaid-cli
      const svgPath = resolve(diagramsDir, `${safeName}.svg`);
      try {
        await execAsync(
          `npx -y @mermaid-js/mermaid-cli mmdc -i "${mmdPath}" -o "${svgPath}"`,
          { timeout: EXEC_TIMEOUT_MS, cwd: _context.cwd },
        );
        return `Diagram saved:\n- Mermaid source: ${mmdPath}\n- SVG: ${svgPath}`;
      } catch {
        return `Diagram saved as Mermaid source: ${mmdPath}\n(SVG generation failed — mmdc not available or errored. Install @mermaid-js/mermaid-cli to enable SVG output.)`;
      }
    } catch (error: any) {
      return `Error creating diagram: ${error.message ?? String(error)}`;
    }
  },
};
