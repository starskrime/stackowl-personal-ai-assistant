import { describe, it, expect } from "vitest";
import { platform as osPlatform } from "node:os";
import { ShellImpl } from "../../src/platform/capabilities/shell.js";

const shell = new ShellImpl();

describe("ShellImpl", () => {
  it("exec runs a trivial echo on the host", async () => {
    const cmd = osPlatform() === "win32" ? "echo hi" : "echo hi";
    const r = await shell.exec(cmd);
    expect(r.exitCode).toBe(0);
    expect(r.stdout.trim()).toBe("hi");
    expect(r.timedOut).toBe(false);
  });

  it("captures stderr separately", async () => {
    const cmd = osPlatform() === "win32"
      ? "powershell -NoProfile -Command \"Write-Error 'oops' -ErrorAction Continue\""
      : "sh -c \"echo oops 1>&2\"";
    const r = await shell.exec(cmd);
    expect(r.stderr).toContain("oops");
  });

  it("respects timeoutMs and reports timedOut=true", async () => {
    const cmd = osPlatform() === "win32"
      ? "powershell -NoProfile -Command \"Start-Sleep -Seconds 5\""
      : "sleep 5";
    const r = await shell.exec(cmd, { timeoutMs: 200 });
    expect(r.timedOut).toBe(true);
  }, 10000);

  it("durationMs is populated", async () => {
    const r = await shell.exec("echo done");
    expect(r.durationMs).toBeGreaterThanOrEqual(0);
  });
});
