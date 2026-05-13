import { describe, it, expect, vi, beforeEach } from "vitest";
import { CriticalToolsGuard, type ApprovalChannel } from "../../src/evolution/critical-tools-guard.js";
import * as os from "node:os";
import * as path from "node:path";
import * as fs from "node:fs";

const tmpDir = path.join(os.tmpdir(), `stackowl-guard-test-${Date.now()}`);
const permissionsFile = path.join(tmpDir, ".permissions.json");

beforeEach(() => {
  fs.mkdirSync(tmpDir, { recursive: true });
  if (fs.existsSync(permissionsFile)) fs.unlinkSync(permissionsFile);
});

const mockChannel: ApprovalChannel = {
  ask: vi.fn().mockResolvedValue(true),
};

describe("CriticalToolsGuard.detectDangerousPatterns", () => {
  it("detects child_process import", () => {
    const code = `import { exec } from "node:child_process";\nexec("rm -rf /");`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("child_process");
  });

  it("detects eval usage", () => {
    const code = `const result = eval(userInput);`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("eval");
  });

  it("detects exec usage without import", () => {
    const code = `execSync("ls -la");`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toContain("exec");
  });

  it("returns empty array for safe code", () => {
    const code = `import { readFile } from "node:fs/promises";\nconst data = await readFile(args.path, "utf-8");\nreturn data;`;
    const patterns = CriticalToolsGuard.detectDangerousPatterns(code);
    expect(patterns).toHaveLength(0);
  });
});

describe("CriticalToolsGuard.check", () => {
  it("returns true without asking when code is safe", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const safe = `const x = 1 + 1;`;
    const result = await guard.check("my_tool", safe);
    expect(result).toBe(true);
    expect(mockChannel.ask).not.toHaveBeenCalled();
  });

  it("asks user when dangerous patterns found", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    const result = await guard.check("my_tool", dangerous);
    expect(result).toBe(true);
    expect(mockChannel.ask).toHaveBeenCalledOnce();
  });

  it("returns false when user denies", async () => {
    const denyChannel: ApprovalChannel = { ask: vi.fn().mockResolvedValue(false) };
    const guard = new CriticalToolsGuard(permissionsFile, denyChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    const result = await guard.check("my_tool", dangerous);
    expect(result).toBe(false);
  });

  it("does not ask again for a previously granted tool", async () => {
    const guard = new CriticalToolsGuard(permissionsFile, mockChannel);
    const dangerous = `import { exec } from "node:child_process"; exec("cmd");`;
    await guard.check("my_tool", dangerous);          // first call — asks
    vi.mocked(mockChannel.ask).mockClear();
    await guard.check("my_tool", dangerous);          // second call — should NOT ask
    expect(mockChannel.ask).not.toHaveBeenCalled();
  });
});
