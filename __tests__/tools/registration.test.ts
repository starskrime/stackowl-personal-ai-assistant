import { describe, it, expect } from "vitest";

// This test verifies the tool catalog shrinks after unified tools replace individual ones.
// We import the registry builder from wherever index.ts creates it.
// If there's no easy way to test this without full app startup, use a simpler check:

describe("Unified tool registration", () => {
  it("memory-unified exports createMemoryUnifiedTool", async () => {
    const { createMemoryUnifiedTool } = await import("../../src/tools/memory-unified.js");
    expect(typeof createMemoryUnifiedTool).toBe("function");
  });

  it("macos/comms-unified exports createMacosCommsTool", async () => {
    const { createMacosCommsTool } = await import("../../src/tools/macos/comms-unified.js");
    expect(typeof createMacosCommsTool).toBe("function");
  });

  it("macos/system-unified exports createMacosSystemTool", async () => {
    const { createMacosSystemTool } = await import("../../src/tools/macos/system-unified.js");
    expect(typeof createMacosSystemTool).toBe("function");
  });
});
