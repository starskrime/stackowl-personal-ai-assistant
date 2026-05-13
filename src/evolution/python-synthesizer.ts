/**
 * StackOwl — Python Tool Synthesizer
 *
 * Generates a complete Python tool file from a CapabilityGap using an LLM.
 * The output follows the Python tool contract so PythonAdapter can wrap it
 * into a ToolImplementation immediately.
 *
 * Python tool contract the LLM must produce:
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

import type { CapabilityGap } from "./detector.js";
import { PythonAnalyzer } from "./python-analyzer.js";
import { log } from "../logger.js";

export interface PythonSynthesisResult {
  toolName: string;
  code: string;
}

const PYTHON_TOOL_CONTRACT = `
Python tool contract — the COMPLETE file must follow this structure:
  # TOOL_NAME: synth_<snake_case_name>  (1-3 word generic name, snake_case)
  # DESCRIPTION: one-line description of what this tool does
  # PARAMETERS:
  #   <param_name>: <type> - <description>
  import json, sys
  # (other safe imports — NO subprocess, NO os.system, NO eval, NO exec)

  def execute(args: dict, cwd: str) -> str:
      # implementation — read args["param_name"] for inputs
      # return a string (JSON-encode complex results)
      ...

  if __name__ == "__main__":
      args = json.loads(sys.argv[1])
      cwd = sys.argv[2]
      print(execute(args, cwd))

FORBIDDEN imports/calls: subprocess, os.system, eval(), exec(), __import__()
All file paths must be relative or derived from args — never hardcoded absolute paths.
`.trim();

/** Minimal provider interface needed by PythonSynthesizer — subset of ModelProvider */
interface MinimalProvider {
  chat(
    messages: Array<{ role: string; content: string }>,
    model: string,
  ): Promise<{ content: string }>;
}

export class PythonSynthesizer {
  async generate(
    gap: CapabilityGap,
    provider: MinimalProvider,
    model: string,
  ): Promise<PythonSynthesisResult> {
    log.synthesis.debug("python-synthesizer.generate: entry", { gap: gap.description });

    const prompt =
      `You are generating a Python tool for StackOwl, an AI assistant framework.\n\n` +
      `Capability needed: ${gap.description}\n` +
      `User request: ${gap.userRequest}\n\n` +
      `${PYTHON_TOOL_CONTRACT}\n\n` +
      `Output ONLY the complete Python file — no markdown fences, no explanation.`;

    const response = await provider.chat([{ role: "user", content: prompt }], model);
    const code = response.content.trim().replace(/^```python\n?|```$/gmu, "").trim();

    const analysis = PythonAnalyzer.analyze(code);
    if (!analysis.safe) {
      log.synthesis.warn("python-synthesizer.generate: unsafe patterns in LLM output", {
        patterns: analysis.patterns,
      });
    }

    const nameMatch = code.match(/^#\s*TOOL_NAME:\s*(\S+)/mu);
    const toolName = nameMatch
      ? nameMatch[1].trim()
      : `synth_${gap.description.slice(0, 20).toLowerCase().replace(/\W+/gu, "_")}`;

    log.synthesis.debug("python-synthesizer.generate: exit", { toolName, codeLen: code.length });
    return { toolName, code };
  }
}
