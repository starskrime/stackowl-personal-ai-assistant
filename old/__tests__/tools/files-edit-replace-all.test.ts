import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EditFileTool } from "../../src/tools/files.js";

let workspace: string;

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-edit-replace-all-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
});

describe("EditFileTool replace_all", () => {
  it("replaces every occurrence when replace_all=true", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo baz foo");
    const result = await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "X", replace_all: true },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("X bar X baz X");
    expect(result).toMatch(/3 occurrence/i);
  });

  it("replaces only the first when replace_all is omitted (back-compat)", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo");
    await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "X" },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("X bar foo");
  });

  it("rejects empty old_string when replace_all=true (would infinite-replace)", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "abc");
    const result = await EditFileTool.execute(
      { path: file, old_string: "", new_string: "X", replace_all: true },
      { cwd: workspace },
    );
    expect(result.toLowerCase()).toMatch(/invalid|empty/);
  });

  it("no-op when old_string === new_string with replace_all=true", async () => {
    const file = join(workspace, "f.txt");
    writeFileSync(file, "foo bar foo");
    const result = await EditFileTool.execute(
      { path: file, old_string: "foo", new_string: "foo", replace_all: true },
      { cwd: workspace },
    );
    expect(readFileSync(file, "utf-8")).toBe("foo bar foo");
    expect(result.toLowerCase()).toMatch(/no-op|0 replacement/);
  });
});
