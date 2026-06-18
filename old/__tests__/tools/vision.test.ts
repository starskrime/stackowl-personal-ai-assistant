// __tests__/tools/vision.test.ts
import { describe, it, expect, vi } from "vitest";

describe("VisionTool", () => {
  it("tool name is 'vision'", async () => {
    const mod = await import("../../src/tools/vision.js");
    expect(mod.VisionTool.definition.name).toBe("vision");
  });

  it("requires imagePath and question parameters", async () => {
    const mod = await import("../../src/tools/vision.js");
    const props = mod.VisionTool.definition.parameters.properties;
    expect(props).toHaveProperty("imagePath");
    expect(props).toHaveProperty("question");
    expect(mod.VisionTool.definition.parameters.required).toContain("imagePath");
    expect(mod.VisionTool.definition.parameters.required).toContain("question");
  });

  it("has capabilities including vision", async () => {
    const mod = await import("../../src/tools/vision.js");
    expect(mod.VisionTool.definition.capabilities).toContain("vision");
  });

  it("returns structured error when provider registry is unavailable", async () => {
    const mod = await import("../../src/tools/vision.js");
    const result = await mod.VisionTool.execute(
      { imagePath: "/tmp/test.png", question: "what is this?" },
      { cwd: "/tmp" }, // no engineContext
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NO_PROVIDER");
  });
});
