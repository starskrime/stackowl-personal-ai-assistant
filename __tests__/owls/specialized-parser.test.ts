import { describe, it, expect, beforeAll } from "vitest";
import { parseSpecializedOwl } from "../../src/owls/specialized-parser.js";
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("parseSpecializedOwl", () => {
  const testDataPath = join(__dirname, "test-data");

  it("should parse a valid specialized_owl.md", () => {
    const content = readFileSync(join(testDataPath, "trading-owl.md"), "utf-8");
    const spec = parseSpecializedOwl(content);
    expect(spec.name).toBe("TradingBot");
    expect(spec.role).toBe("Stock trading assistant");
    expect(spec.emoji).toBe("📈");
    expect(spec.personality.challengeLevel).toBe("high");
    expect(spec.permissions.deniedTools).toContain("write");
    expect(spec.permissions.allowedTools).toContain("shell");
  });

  it("should parse expertise domains", () => {
    const content = readFileSync(join(testDataPath, "trading-owl.md"), "utf-8");
    const spec = parseSpecializedOwl(content);
    expect(spec.expertise).toContain("stock market analysis");
    expect(spec.routingRules.keywords).toContain("trading");
  });

  it("should parse model config", () => {
    const content = readFileSync(join(testDataPath, "trading-owl.md"), "utf-8");
    const spec = parseSpecializedOwl(content);
    expect(spec.model.provider).toBe("anthropic");
    expect(spec.model.model).toBe("claude-sonnet-4-20250514");
    expect(spec.model.maxTokens).toBe(4096);
  });

  it("should use defaults for missing fields", () => {
    const minimalContent = `---
name: MinimalOwl
role: Minimal assistant
emoji: 🦉
---
`;
    const spec = parseSpecializedOwl(minimalContent);
    expect(spec.name).toBe("MinimalOwl");
    expect(spec.personality.challengeLevel).toBe("medium");
    expect(spec.personality.verbosity).toBe("balanced");
    expect(spec.model.provider).toBe("openai");
  });

  it("should throw when name field is missing from frontmatter", () => {
    const noName = `---
role: Some role
emoji: 🦉
---
`;
    expect(() => parseSpecializedOwl(noName)).toThrow("missing required field: name");
  });

  it("should throw when frontmatter is empty", () => {
    const empty = `---
---
`;
    expect(() => parseSpecializedOwl(empty)).toThrow("missing required field: name");
  });

  it("should throw when content has no frontmatter at all", () => {
    const noFrontmatter = `# Just a markdown heading

Some content without frontmatter.
`;
    expect(() => parseSpecializedOwl(noFrontmatter)).toThrow("missing required field: name");
  });
});
