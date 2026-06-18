import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";
import { ReadFileTool } from "../../src/tools/files.js";

let workspace: string;
let external: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-files-sandbox-"));
  external = mkdtempSync(join(homedir(), ".stackowl-files-external-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  rmSync(external, { recursive: true, force: true });
});

describe("files.ts sandbox (regression)", () => {
  it("blocks symlink escape — symlink inside workspace pointing outside is rejected", async () => {
    const secret = join(external, "secret.txt");
    writeFileSync(secret, "TOP-SECRET");
    const link = join(workspace, "innocent.txt");
    try {
      symlinkSync(secret, link);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "EPERM") return; // Windows w/o admin
      throw e;
    }

    const result = await ReadFileTool.execute({ path: link }, { cwd: workspace });
    expect(result).not.toContain("TOP-SECRET");
    expect(result.toLowerCase()).toMatch(/access denied|outside/);
  });

  it("allows reading a normal file inside the workspace", async () => {
    const normal = join(workspace, "ok.txt");
    writeFileSync(normal, "hello");
    const result = await ReadFileTool.execute({ path: normal }, { cwd: workspace });
    expect(result).toContain("hello");
  });
});
