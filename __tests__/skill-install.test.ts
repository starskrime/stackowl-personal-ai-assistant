import { describe, it, expect } from "vitest";
import { SkillInstallTool } from "../src/tools/skill-install.js";

describe("SkillInstallTool", () => {
  it("has the correct tool name", () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    expect(tool.definition.name).toBe("install_skill");
  });

  it("has a source parameter in its schema", () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const props = tool.definition.parameters?.properties as Record<string, unknown>;
    expect(props).toHaveProperty("source");
  });

  it("execute() returns a string (never throws) on a non-existent local path", async () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const result = await tool.execute(
      { source: "./non-existent-skill-path-xyz" },
      { cwd: "/tmp" },
    );
    expect(typeof result).toBe("string");
    expect(result.length).toBeGreaterThan(0);
  });

  it("execute() returns error string on bad GitHub slug without network", async () => {
    const tool = new SkillInstallTool("/tmp/workspace");
    const result = await tool.execute(
      { source: "github:bad-user/bad-repo/bad/path" },
      { cwd: "/tmp" },
    );
    expect(typeof result).toBe("string");
  });
});
