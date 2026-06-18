import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { tmpdir, homedir } from "node:os";
import { SandboxImpl } from "../../src/platform/capabilities/sandbox.js";
import { PathsImpl } from "../../src/platform/capabilities/paths.js";

let workspace: string;
let external: string;
const paths = new PathsImpl();
const sandbox = new SandboxImpl(paths);

beforeEach(() => {
  workspace = mkdtempSync(join(tmpdir(), "stackowl-sandbox-test-"));
  external = mkdtempSync(join(homedir(), ".stackowl-sandbox-external-"));
});

afterEach(() => {
  rmSync(workspace, { recursive: true, force: true });
  rmSync(external, { recursive: true, force: true });
});

describe("SandboxImpl.check", () => {
  it("allows a file inside a workspace root", () => {
    const file = join(workspace, "ok.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(true);
    expect(r.reason).toBeUndefined();
  });

  it("rejects a file outside workspace roots", () => {
    const file = join(external, "external.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_OUTSIDE_SANDBOX");
  });

  it("rejects tempdir paths when allowTempdir is false (default)", () => {
    const r = sandbox.check(join(tmpdir(), "x.db"), { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
  });

  it("allows tempdir paths when allowTempdir is true", () => {
    const file = join(tmpdir(), "stackowl-sandbox-allowed-" + Date.now() + ".db");
    writeFileSync(file, "");
    try {
      const r = sandbox.check(file, { workspaceRoots: [workspace], allowTempdir: true });
      expect(r.ok).toBe(true);
    } finally {
      rmSync(file, { force: true });
    }
  });

  it("enforces allowExtensions whitelist", () => {
    const file = join(workspace, "data.txt");
    writeFileSync(file, "");
    const r = sandbox.check(file, {
      workspaceRoots: [workspace],
      allowExtensions: [".db", ".sqlite"],
    });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_EXTENSION_BLOCKED");
  });

  it("symlink escape: symlink inside workspace pointing outside is rejected", () => {
    const target = join(external, "secret.db");
    writeFileSync(target, "");
    const link = join(workspace, "evil.db");
    try {
      symlinkSync(target, link);
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "EPERM") return;
      throw e;
    }
    const r = sandbox.check(link, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe("E_OUTSIDE_SANDBOX");
  });

  it("missing file falls back to lexical path (does not throw)", () => {
    const file = join(workspace, "future.db");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.ok).toBe(true);
  });

  it("resolves relative paths against cwd via path.resolve", () => {
    const r = sandbox.check("subdir/file.db", { workspaceRoots: [workspace] });
    expect(r.resolvedPath.startsWith("/") || /^[A-Za-z]:/.test(r.resolvedPath)).toBe(true);
  });

  it("returns the resolved (post-realpath) path in result", () => {
    const file = join(workspace, "x.db");
    writeFileSync(file, "");
    const r = sandbox.check(file, { workspaceRoots: [workspace] });
    expect(r.resolvedPath).toContain("x.db");
  });
});
