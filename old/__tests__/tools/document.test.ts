// __tests__/tools/document.test.ts
import { describe, it, expect } from "vitest";
import { writeFile, rm, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("DocumentTool", () => {
  it("tool name is 'document'", async () => {
    const mod = await import("../../src/tools/document.js");
    expect(mod.DocumentTool.definition.name).toBe("document");
  });

  it("has action parameter with parse, extract_tables, metadata enum values", async () => {
    const mod = await import("../../src/tools/document.js");
    const props = mod.DocumentTool.definition.parameters.properties;
    expect(props.action.enum).toEqual(
      expect.arrayContaining(["parse", "extract_tables", "metadata"]),
    );
  });

  it("parse action returns text for a markdown file", async () => {
    const mod = await import("../../src/tools/document.js");

    const dir = await mkdtemp(join(tmpdir(), "doc-test-"));
    const mdPath = join(dir, "test.md");
    await writeFile(mdPath, "# Hello\nWorld content here.");

    const result = await mod.DocumentTool.execute(
      { action: "parse", filePath: mdPath },
      { cwd: dir },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.text).toContain("Hello");

    await rm(dir, { recursive: true });
  });

  it("unsupported extension returns structured error", async () => {
    const mod = await import("../../src/tools/document.js");

    const result = await mod.DocumentTool.execute(
      { action: "parse", filePath: "/tmp/file.xyz" },
      { cwd: "/tmp" },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("UNSUPPORTED_FORMAT");
  });
});
