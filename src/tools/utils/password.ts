import { randomBytes } from "node:crypto";
import type { ToolImplementation, ToolContext } from "../registry.js";

export const PasswordGeneratorTool: ToolImplementation = {
  definition: {
    name: "generate_password",
    description: "Generate a cryptographically secure random password.",
    parameters: {
      type: "object",
      properties: {
        length: {
          type: "number",
          description: "Password length (default: 16)",
        },
        includeSymbols: {
          type: "boolean",
          description: "Include symbols like !@#$%^&* (default: true)",
        },
        includeNumbers: {
          type: "boolean",
          description: "Include numbers (default: true)",
        },
      },
      required: [],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const length = args.length ? Number(args.length) : 16;
      const includeSymbols = args.includeSymbols !== false;
      const includeNumbers = args.includeNumbers !== false;

      if (!isFinite(length) || length < 4 || length > 256) {
        return "Error: Password length must be between 4 and 256.";
      }

      let charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
      if (includeNumbers) {
        charset += "0123456789";
      }
      if (includeSymbols) {
        charset += "!@#$%^&*()-_=+[]{}|;:,.<>?";
      }

      const bytes = randomBytes(length);
      let password = "";
      for (let i = 0; i < length; i++) {
        password += charset[bytes[i]! % charset.length];
      }

      return `Generated password (${length} chars):\n${password}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error generating password: ${msg}`;
    }
  },
};
