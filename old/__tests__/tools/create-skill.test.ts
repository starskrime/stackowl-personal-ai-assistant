import { describe, it, expect, afterEach } from "vitest";
import { mkdir, rm, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { CreateSkillTool } from "../../src/tools/create-skill.js";

const TEST_SKILLS_DIR = join(tmpdir(), `stackowl-test-skills-${Date.now()}`);

async function makeTool() {
  await mkdir(TEST_SKILLS_DIR, { recursive: true });
  return new CreateSkillTool(TEST_SKILLS_DIR);
}

afterEach(async () => {
  await rm(TEST_SKILLS_DIR, { recursive: true, force: true });
});

describe("CreateSkillTool", () => {
  it("writes SKILL.md to the correct path", async () => {
    const tool = await makeTool();
    await tool.execute({
      name: "my_skill",
      description: "Does something useful",
      instructions: "## Steps\n1. Do the thing.",
    });
    const content = await readFile(
      join(TEST_SKILLS_DIR, "my_skill", "SKILL.md"),
      "utf-8",
    );
    expect(content).toContain("name: my_skill");
    expect(content).toContain("Does something useful");
    expect(content).toContain("## Steps");
  });

  it("rejects names that are not snake_case alphanumeric", async () => {
    const tool = await makeTool();
    await expect(
      tool.execute({ name: "My Skill!", description: "bad", instructions: "x".repeat(10) }),
    ).rejects.toThrow(/invalid name/i);
  });

  it("rejects description longer than 64 characters", async () => {
    const tool = await makeTool();
    await expect(
      tool.execute({ name: "fine_name", description: "a".repeat(65), instructions: "x".repeat(10) }),
    ).rejects.toThrow(/description too long/i);
  });

  it("returns skill name and path on success", async () => {
    const tool = await makeTool();
    const result = await tool.execute({
      name: "quick_one",
      description: "Quick test skill",
      instructions: "Do the quick thing.",
    });
    expect(result).toContain("quick_one");
    expect(result).toContain("SKILL.md");
  });
});
