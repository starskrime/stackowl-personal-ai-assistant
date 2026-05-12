import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { GitTool } from "../../../src/tools/dev/git.js";

let repo: string;

function git(repo: string, ...args: string[]): { stdout: string; status: number } {
  const r = spawnSync("git", args, { cwd: repo, encoding: "utf-8" });
  return { stdout: r.stdout, status: r.status ?? 1 };
}

beforeEach(() => {
  repo = mkdtempSync(join(tmpdir(), "stackowl-git-writes-"));
  git(repo, "init", "-b", "main");
  git(repo, "config", "user.email", "test@stackowl.local");
  git(repo, "config", "user.name", "Test");
});

afterEach(() => {
  rmSync(repo, { recursive: true, force: true });
});

describe("GitTool writes (add/commit/fetch/push/pull)", () => {
  it("add stages files", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    const res = await GitTool.execute({ action: "add", paths: ["a.txt"] }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "diff", "--cached", "--name-only").stdout.trim()).toBe("a.txt");
  });

  it("commit records the message", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    const res = await GitTool.execute({ action: "commit", message: "test: initial" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "log", "-1", "--pretty=%s").stdout.trim()).toBe("test: initial");
  });

  it("commit with nothing staged returns an error", async () => {
    const res = await GitTool.execute({ action: "commit", message: "empty" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
  });

  it("fetch attempts the remote (errors clearly when no remote configured)", async () => {
    const res = await GitTool.execute({ action: "fetch" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(typeof parsed.error.message).toBe("string");
  });

  it("push without i_understand_destructive blocks --force", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "x" }, { cwd: repo } as any);
    const res = await GitTool.execute({ action: "push", force: true }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });

  it("push with i_understand_destructive proceeds past the gate", async () => {
    writeFileSync(join(repo, "a.txt"), "hello");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "x" }, { cwd: repo } as any);
    const res = await GitTool.execute({ action: "push", force: true, i_understand_destructive: true }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).not.toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });

  it("pull without remote errors clearly", async () => {
    const res = await GitTool.execute({ action: "pull" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
  });

  it("checkout creates and switches to a new branch", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo } as any);
    const res = await GitTool.execute({ action: "checkout", target: "feature", create_branch: true }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "branch", "--show-current").stdout.trim()).toBe("feature");
  });

  it("merge --abort cancels an in-progress merge", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo } as any);
    await GitTool.execute({ action: "checkout", target: "branch-b", create_branch: true }, { cwd: repo } as any);
    writeFileSync(join(repo, "a.txt"), "y");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "b" }, { cwd: repo } as any);
    await GitTool.execute({ action: "checkout", target: "main" }, { cwd: repo } as any);
    writeFileSync(join(repo, "a.txt"), "z");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "a" }, { cwd: repo } as any);

    await GitTool.execute({ action: "merge", branch: "branch-b" }, { cwd: repo } as any);
    const abort = await GitTool.execute({ action: "merge", abort: true }, { cwd: repo } as any);
    const parsed = JSON.parse(abort);
    expect(parsed.success).toBe(true);
  });

  it("reset mixed (default) unstages", async () => {
    writeFileSync(join(repo, "a.txt"), "x");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    await GitTool.execute({ action: "commit", message: "init" }, { cwd: repo } as any);
    writeFileSync(join(repo, "a.txt"), "y");
    await GitTool.execute({ action: "add", paths: ["."] }, { cwd: repo } as any);
    const res = await GitTool.execute({ action: "reset", target: "HEAD" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(git(repo, "diff", "--cached", "--name-only").stdout.trim()).toBe("");
  });

  it("reset --hard without i_understand_destructive is blocked", async () => {
    const res = await GitTool.execute({ action: "reset", target: "HEAD", mode: "hard" }, { cwd: repo } as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("DESTRUCTIVE_ACTION_BLOCKED");
  });
});
