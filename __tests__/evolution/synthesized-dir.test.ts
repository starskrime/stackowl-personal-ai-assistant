import { describe, it, expect } from "vitest";
import { getSynthesizedDir } from "../../src/evolution/synthesizer.js";
import type { StackOwlConfig } from "../../src/config/loader.js";

describe("getSynthesizedDir", () => {
  it("returns config value when synthesizedDir is set", () => {
    const config = { synthesis: { synthesizedDir: "/custom/path/synth" } } as unknown as StackOwlConfig;
    expect(getSynthesizedDir(config)).toBe("/custom/path/synth");
  });

  it("falls back to workspace/synthesized when not configured", () => {
    const config = { workspace: "./my-workspace", synthesis: {} } as unknown as StackOwlConfig;
    const result = getSynthesizedDir(config);
    expect(result).toContain("my-workspace");
    expect(result).toContain("synthesized");
  });

  it("falls back to workspace/synthesized when synthesis block is absent", () => {
    const config = { workspace: "./my-workspace" } as unknown as StackOwlConfig;
    const result = getSynthesizedDir(config);
    expect(result).toContain("synthesized");
  });
});
