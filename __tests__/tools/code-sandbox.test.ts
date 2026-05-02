// __tests__/tools/code-sandbox.test.ts
import { describe, it, expect } from "vitest";

describe("CodeSandboxTool", () => {
  it("tool name is 'sandbox'", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    expect(mod.CodeSandboxTool.definition.name).toBe("sandbox");
  });

  it("language param has enum python | javascript", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    const langProp = mod.CodeSandboxTool.definition.parameters.properties.language;
    expect(langProp.enum).toContain("python");
    expect(langProp.enum).toContain("javascript");
  });

  it("executes simple javascript and returns stdout", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    const result = await mod.CodeSandboxTool.execute(
      { language: "javascript", code: "console.log('hello sandbox')" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.stdout).toContain("hello sandbox");
    expect(parsed.data.exitCode).toBe(0);
  }, 10_000);

  it("times out when timeout is exceeded", async () => {
    const mod = await import("../../src/tools/code-sandbox.js");
    const result = await mod.CodeSandboxTool.execute(
      {
        language: "javascript",
        code: "const start=Date.now(); while(Date.now()-start<5000){}",
        timeout: 500,
      },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("TIMEOUT");
  }, 10_000);
});
