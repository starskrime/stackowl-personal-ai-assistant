import { describe, it, expect } from "vitest";
import { SkillsRegistry } from "../../src/skills/registry.js";
import type { Skill } from "../../src/skills/types.js";

function makeSkill(name: string, desc: string): Skill {
  return {
    name,
    description: desc,
    instructions: "Very long instructions that should NOT appear in the header.",
    metadata: { name, description: desc },
    sourcePath: `/tmp/skills/${name}/SKILL.md`,
    enabled: true,
  };
}

describe("SkillsRegistry.formatSkillsHeader", () => {
  it("emits name and description but NOT instructions", () => {
    const registry = new SkillsRegistry();
    registry.register(makeSkill("git_commit", "Stage changed files and commit"));
    const skills = registry.listEnabled();
    const header = registry.formatSkillsHeader(skills);

    expect(header).toContain("<name>git_commit</name>");
    expect(header).toContain(
      "<description>Stage changed files and commit</description>"
    );
    expect(header).not.toContain("Very long instructions");
  });

  it("returns empty string when skills list is empty", () => {
    const registry = new SkillsRegistry();
    expect(registry.formatSkillsHeader([])).toBe("");
  });

  it("includes correct XML structure", () => {
    const registry = new SkillsRegistry();
    registry.register(makeSkill("web_research", "Search the web"));
    const header = registry.formatSkillsHeader(registry.listEnabled());
    expect(header).toMatch(/<available_skills>/);
    expect(header).toMatch(/<\/available_skills>/);
  });

  it("handles multiple skills and includes both names", () => {
    const registry = new SkillsRegistry();
    registry.register(makeSkill("skill_one", "First skill description"));
    registry.register(makeSkill("skill_two", "Second skill description"));
    const header = registry.formatSkillsHeader(registry.listEnabled());

    expect(header).toContain("<name>skill_one</name>");
    expect(header).toContain("<name>skill_two</name>");
    expect(header).toContain("<description>First skill description</description>");
    expect(header).toContain("<description>Second skill description</description>");
  });
});
