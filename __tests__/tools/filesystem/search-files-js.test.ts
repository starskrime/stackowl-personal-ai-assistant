import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SearchFilesTool } from "../../../src/tools/filesystem/search-files.js";

let workspace: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-search-files-js-"));
  process.env.STACKOWL_DISABLE_RG = "true";
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  delete process.env.STACKOWL_DISABLE_RG;
});

describe("SearchFilesTool (JS fallback)", () => {
  it("literal match finds occurrences", async () => {
    writeFileSync(join(workspace, "a.ts"), "const x = 1;\nconst foo = 2;\nconst y = 3;");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(1);
    expect(parsed.data.matches[0].line).toBe(2);
    expect(parsed.data.via).toBe("js-fallback");
  });

  it("regex=true treats pattern as regex", async () => {
    writeFileSync(join(workspace, "a.ts"), "abc123\nxyz999\nfoo456");
    const res = await SearchFilesTool.execute({ pattern: "\\d{3}", path: workspace, regex: true }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(3);
  });

  it("case_sensitive=false matches mixed case", async () => {
    writeFileSync(join(workspace, "a.ts"), "Foo\nFOO\nfoo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(3);
  });

  it("glob restricts file extensions", async () => {
    writeFileSync(join(workspace, "a.ts"), "foo");
    writeFileSync(join(workspace, "b.js"), "foo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace, glob: "*.ts" }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.length).toBe(1);
    expect(parsed.data.matches[0].path).toBe("a.ts");
  });

  it("skips binary files (null byte in first 8KB)", async () => {
    writeFileSync(join(workspace, "binary.bin"), Buffer.from([0x66, 0x6f, 0x6f, 0x00, 0x66]));
    writeFileSync(join(workspace, "text.txt"), "foo");
    const res = await SearchFilesTool.execute({ pattern: "foo", path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches.every((m: any) => !m.path.endsWith(".bin"))).toBe(true);
  });

  it("context_lines returns surrounding lines", async () => {
    writeFileSync(join(workspace, "a.txt"), "line1\nline2\nMATCH\nline4\nline5");
    const res = await SearchFilesTool.execute({ pattern: "MATCH", path: workspace, context_lines: 1 }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.matches[0].before).toEqual(["line2"]);
    expect(parsed.data.matches[0].after).toEqual(["line4"]);
  });
});
