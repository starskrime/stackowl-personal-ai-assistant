import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const CredentialsTool: ToolImplementation = {
  definition: {
    name: "credentials_get",
    description:
      "Retrieve a credential value by key name. " +
      "Each helper can only access credentials in its own folder. " +
      "Use this when you need an API key or token to perform an action.",
    parameters: {
      type: "object",
      properties: {
        key: {
          type: "string",
          description: "The credential key to retrieve (e.g., ALPHA_VANTAGE_KEY)",
        },
        owlName: {
          type: "string",
          description: "The owl name whose credentials to access",
        },
      },
      required: ["key", "owlName"],
    },
  },
  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const key = args.key as string;
    const owlName = args.owlName as string;

    if (!key || !owlName) {
      return JSON.stringify({ error: "Missing key or owlName parameter" });
    }

    // Allowlist: only letters, digits, hyphens, underscores
    const safeOwlName = owlName.replace(/[^a-zA-Z0-9_-]/g, "");
    if (!safeOwlName || safeOwlName !== owlName) {
      log.tool.error(`[CredentialsTool] Path traversal attempt: ${owlName}`);
      return JSON.stringify({ error: "Access denied: invalid owl name" });
    }

    const resolvedBase = resolve(join(context.cwd, "workspace"));
    const credentialsPath = resolve(join(resolvedBase, "owls", safeOwlName, "credentials", "secrets.md"));

    if (!credentialsPath.startsWith(resolvedBase + "/")) {
      log.tool.error(`[CredentialsTool] Path traversal attempt: ${credentialsPath}`);
      return JSON.stringify({ error: "Access denied: invalid owl name" });
    }

    if (!existsSync(credentialsPath)) {
      return JSON.stringify({ error: `Credentials file not found for ${owlName}` });
    }

    try {
      const content = readFileSync(credentialsPath, "utf-8");
      const lines = content.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith("#") || trimmed === "") continue;
        const [k, ...vParts] = trimmed.split("=");
        if (k.trim() === key) {
          return JSON.stringify({ key, value: vParts.join("=").trim() });
        }
      }
      return JSON.stringify({ error: `Key '${key}' not found in ${owlName} credentials` });
    } catch (error) {
      log.tool.error(`[CredentialsTool] Failed to read credentials: ${error}`);
      return JSON.stringify({ error: "Failed to read credentials" });
    }
  },
};
