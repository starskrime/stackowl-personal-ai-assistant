import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";

export const CalculatorTool: ToolImplementation = {
  definition: {
    name: "calculator",
    description:
      "Calculate math expressions accurately. Use this instead of doing mental math. Supports: arithmetic, sqrt, pow, sin, cos, tan, log, PI, E.",
    parameters: {
      type: "object",
      properties: {
        expression: {
          type: "string",
          description:
            "The math expression to evaluate, e.g. '2 + 3 * 4' or 'Math.sqrt(144)' or 'Math.PI * Math.pow(5, 2)'",
        },
      },
      required: ["expression"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const expression = String(args.expression);
    log.tool.debug("calculator.execute: entry", { expression });
    try {

      // Sanitize: only allow safe characters and Math.xxx references
      const sanitized = expression.replace(/\s+/g, " ").trim();
      // Validate each token
      const tokens = sanitized.split(/(\s+)/);
      for (const token of tokens) {
        const trimmed = token.trim();
        if (!trimmed) continue;
        // Allow: digits, operators, parens, dots, commas, Math.xxx
        if (
          !/^[\d.]+$/.test(trimmed) &&
          !/^[+\-*/^%(),]+$/.test(trimmed) &&
          !/^Math\.(sqrt|pow|abs|ceil|floor|round|min|max|sin|cos|tan|asin|acos|atan|atan2|log|log2|log10|exp|PI|E|sign|trunc|cbrt|hypot)$/.test(
            trimmed,
          )
        ) {
          // Check if it's a compound expression like "Math.sqrt(144)"
          const compoundCheck = trimmed.replace(
            /Math\.(sqrt|pow|abs|ceil|floor|round|min|max|sin|cos|tan|asin|acos|atan|atan2|log|log2|log10|exp|PI|E|sign|trunc|cbrt|hypot)/g,
            "",
          );
          if (!/^[\d.+\-*/^%(),\s]*$/.test(compoundCheck)) {
            return `Error: Expression contains disallowed characters or functions: "${trimmed}"`;
          }
        }
      }

      // Replace ^ with ** for exponentiation
      const prepared = sanitized.replace(/\^/g, "**");

      const result = new Function("Math", "return " + prepared)(Math);

      if (typeof result !== "number" || !isFinite(result)) {
        return `Error: Expression evaluated to an invalid number (${result})`;
      }

      log.tool.debug("calculator.execute: exit", { success: true, result });
      return `${expression} = ${result}`;
    } catch (error) {
      log.tool.error("calculator.execute: failed", error, { expression });
      const msg = error instanceof Error ? error.message : String(error);
      return `Error evaluating expression: ${msg}`;
    }
  },
};
