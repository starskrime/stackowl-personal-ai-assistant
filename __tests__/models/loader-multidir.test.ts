import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ModelLoader, resetModelLoader } from "../../src/models/loader.js";

function writeTempModel(dir: string, name: string, content: string): void {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, name), content, "utf-8");
}

describe("ModelLoader — multi-directory", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = join(tmpdir(), `loader-test-${Date.now()}`);
    resetModelLoader();
  });

  afterEach(() => {
    try { rmSync(tmpDir, { recursive: true }); } catch { /* ok */ }
    resetModelLoader();
  });

  it("loads models from a user directory in addition to system dir", () => {
    writeTempModel(tmpDir, "my-custom", "compatible: openai\nurl: \"http://localhost:9999/v1\"\ndefaultModel: \"custom-model\"\navailableModels: [\"custom-model\"]");
    const loader = new ModelLoader([tmpDir]);
    const def = loader.get("my-custom");
    expect(def).not.toBeNull();
    expect(def?.compatible).toBe("openai");
    expect(def?.url).toBe("http://localhost:9999/v1");
  });

  it("system names are reserved — user dir file with system name is ignored", () => {
    // "anthropic" is a system name
    writeTempModel(tmpDir, "anthropic", "compatible: anthropic\nurl: \"http://fake.com/v1\"\ndefaultModel: \"fake-model\"\navailableModels: [\"fake-model\"]");
    const loader = new ModelLoader([tmpDir]);
    // System anthropic definition must still be the real one
    const def = loader.get("anthropic");
    expect(def?.url).toContain("api.anthropic.com");
  });

  it("isSystemName() returns true for built-in providers", () => {
    const loader = new ModelLoader();
    expect(loader.isSystemName("anthropic")).toBe(true);
    expect(loader.isSystemName("openai")).toBe(true);
    expect(loader.isSystemName("ollama")).toBe(true);
  });

  it("isSystemName() returns false for user-added providers", () => {
    writeTempModel(tmpDir, "my-llm", "compatible: openai\nurl: \"http://localhost:9999/v1\"\ndefaultModel: \"llm\"\navailableModels: [\"llm\"]");
    const loader = new ModelLoader([tmpDir]);
    expect(loader.isSystemName("my-llm")).toBe(false);
  });

  it("user directory is non-blocking when it does not exist", () => {
    const nonExistentDir = join(tmpDir, "no-such-dir");
    expect(() => new ModelLoader([nonExistentDir])).not.toThrow();
  });
});
