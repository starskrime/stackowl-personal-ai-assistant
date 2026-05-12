import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { ListDirectoryTool } from "../../../src/tools/filesystem/list-directory.js";

let workspace: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-list-dir-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
});

function setup(structure: Record<string, string | null>) {
  for (const [relpath, contents] of Object.entries(structure)) {
    const abs = join(workspace, relpath);
    if (contents === null) {
      mkdirSync(abs, { recursive: true });
    } else {
      mkdirSync(dirname(abs), { recursive: true });
      writeFileSync(abs, contents);
    }
  }
}

describe("ListDirectoryTool", () => {
  it("flat listing returns top-level entries only", async () => {
    setup({ "a.txt": "x", "b.txt": "x", "sub/c.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    const names = parsed.data.entries.map((e: any) => e.path);
    expect(names).toContain("a.txt");
    expect(names).toContain("b.txt");
    expect(names).toContain("sub");
    expect(names).not.toContain("sub/c.txt");
  });

  it("recursive=true descends", async () => {
    setup({ "a.txt": "x", "sub/c.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, recursive: true }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    const names = parsed.data.entries.map((e: any) => e.path);
    expect(names).toContain("sub/c.txt");
  });

  it("glob filters to matching files", async () => {
    setup({ "a.ts": "x", "b.js": "x", "sub/c.ts": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, glob: "**/*.ts" }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    const names = parsed.data.entries.map((e: any) => e.path);
    expect(names).toContain("a.ts");
    expect(names).toContain("sub/c.ts");
    expect(names).not.toContain("b.js");
  });

  it("hides dotfiles by default", async () => {
    setup({ ".env": "x", "visible.txt": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    const names = parsed.data.entries.map((e: any) => e.path);
    expect(names).not.toContain(".env");
    expect(names).toContain("visible.txt");
  });

  it("include_hidden=true shows dotfiles", async () => {
    setup({ ".env": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, include_hidden: true }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.entries.map((e: any) => e.path)).toContain(".env");
  });

  it("hard-excludes node_modules even with include_hidden", async () => {
    setup({ "node_modules/lodash/index.js": "x", "src/main.ts": "x" });
    const res = await ListDirectoryTool.execute({ path: workspace, recursive: true, include_hidden: true }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    const names = parsed.data.entries.map((e: any) => e.path);
    expect(names.some((n: string) => n.startsWith("node_modules"))).toBe(false);
    expect(names).toContain("src/main.ts");
  });

  it("max_results truncates and reports truncated=true", async () => {
    const structure: Record<string, string> = {};
    for (let i = 0; i < 20; i++) structure[`f${i}.txt`] = "x";
    setup(structure);
    const res = await ListDirectoryTool.execute({ path: workspace, max_results: 5 }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.entries.length).toBe(5);
    expect(parsed.data.truncated).toBe(true);
  });

  it("rejects paths outside the workspace via platform.sandbox", async () => {
    const res = await ListDirectoryTool.execute({ path: "/etc" }, { cwd: workspace } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACCESS_DENIED");
  });
});
