#!/usr/bin/env tsx
// scripts/create-tool.ts
/**
 * Tool scaffolder.
 * Usage: npm run tool:create <tool-name> <category>
 * Example: npm run tool:create my_analyzer cognitive
 *
 * Creates:
 *   src/tools/<tool-name>.ts
 *   __tests__/tools/<tool-name>.test.ts
 */
import { writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

const VALID_CATEGORIES = [
  "filesystem", "shell", "network", "system", "cognitive", "mcp",
];

async function main() {
  const [, , rawName, rawCategory] = process.argv;

  if (!rawName || !rawCategory) {
    console.error("Usage: npm run tool:create <tool-name> <category>");
    console.error(`Valid categories: ${VALID_CATEGORIES.join(", ")}`);
    process.exit(1);
  }

  const name     = rawName.replace(/[^a-zA-Z0-9_]/g, "_");
  const category = rawCategory.toLowerCase();

  if (!VALID_CATEGORIES.includes(category)) {
    console.error(`Invalid category "${category}". Valid: ${VALID_CATEGORIES.join(", ")}`);
    process.exit(1);
  }

  const className = name
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join("") + "Tool";

  const toolPath = resolve(join("src", "tools", `${name}.ts`));
  const testPath = resolve(join("__tests__", "tools", `${name}.test.ts`));

  if (existsSync(toolPath)) {
    console.error(`Error: ${toolPath} already exists.`);
    process.exit(1);
  }

  const toolContent = `// src/tools/${name}.ts
import type { ToolImplementation, ToolContext } from "./registry.js";

export const ${className}: ToolImplementation = {
  definition: {
    name: "${name}",
    description:
      "TODO: Describe what this tool does. " +
      'Example: ${name}(param: "value")',
    parameters: {
      type: "object",
      properties: {
        input: {
          type: "string",
          description: "TODO: Describe this parameter.",
        },
      },
      required: ["input"],
    },
    capabilities: ["TODO"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 1 },
  },

  category: "${category}",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const input = args["input"] as string;

    if (!input) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "input is required" } });
    }

    // TODO: Implement
    return JSON.stringify({ success: true, data: { result: input } });
  },
};
`;

  const testContent = `// __tests__/tools/${name}.test.ts
import { describe, it, expect } from "vitest";

describe("${className}", () => {
  it("tool name is '${name}'", async () => {
    const mod = await import("../../src/tools/${name}.js");
    expect(mod.${className}.definition.name).toBe("${name}");
  });

  it("executes successfully with valid input", async () => {
    const mod = await import("../../src/tools/${name}.js");
    const result = await mod.${className}.execute(
      { input: "test value" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
  });

  it("returns structured error when input is missing", async () => {
    const mod = await import("../../src/tools/${name}.js");
    const result = await mod.${className}.execute(
      {},
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("MISSING_ARG");
  });
});
`;

  await mkdir(resolve("src", "tools"), { recursive: true });
  await mkdir(resolve("__tests__", "tools"), { recursive: true });

  await writeFile(toolPath, toolContent, "utf-8");
  await writeFile(testPath, testContent, "utf-8");

  console.log(`✅ Created tool: ${toolPath}`);
  console.log(`✅ Created test: ${testPath}`);
  console.log(`\nNext steps:`);
  console.log(`  1. Implement execute() in ${toolPath}`);
  console.log(`  2. Register in src/index.ts`);
  console.log(`  3. Run: npx vitest run ${testPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
