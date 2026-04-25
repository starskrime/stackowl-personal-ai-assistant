import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillInstaller, parseInstallSource } from "../src/skills/installer.js";
import * as fsp from "node:fs/promises";
import * as fs from "node:fs";

vi.mock("node:fs/promises");
vi.mock("node:fs");
vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

describe("parseInstallSource", () => {
  it("parses github: prefix", () => {
    const result = parseInstallSource("github:anthropics/superpowers/skills/tdd");
    expect(result.type).toBe("github");
    expect(result.rawUrl).toBe(
      "https://raw.githubusercontent.com/anthropics/superpowers/main/skills/tdd/SKILL.md",
    );
    expect(result.skillName).toBe("tdd");
  });

  it("parses github: with branch pin", () => {
    const result = parseInstallSource("github:user/repo/path/to/skill@dev");
    expect(result.type).toBe("github");
    expect(result.rawUrl).toBe(
      "https://raw.githubusercontent.com/user/repo/dev/path/to/skill/SKILL.md",
    );
    expect(result.skillName).toBe("skill");
  });

  it("parses local ./ prefix", () => {
    const result = parseInstallSource("./my-skills/cost_alarm");
    expect(result.type).toBe("local");
    expect(result.localPath).toContain("cost_alarm");
    expect(result.skillName).toBe("cost_alarm");
  });

  it("parses local absolute path", () => {
    const result = parseInstallSource("/home/user/skills/my_skill");
    expect(result.type).toBe("local");
    expect(result.skillName).toBe("my_skill");
  });

  it("returns clawhub type for plain slugs", () => {
    const result = parseInstallSource("git_commit");
    expect(result.type).toBe("clawhub");
    expect(result.slug).toBe("git_commit");
  });

  it("returns clawhub type for clawhub: prefix", () => {
    const result = parseInstallSource("clawhub:git_commit");
    expect(result.type).toBe("clawhub");
    expect(result.slug).toBe("git_commit");
  });
});

describe("SkillInstaller.fromLocal", () => {
  beforeEach(() => vi.resetAllMocks());

  it("copies SKILL.md from local path to target dir", async () => {
    vi.mocked(fs.existsSync).mockImplementation((p) => {
      return String(p).includes("SKILL.md") || String(p).includes("cost_alarm");
    });
    vi.mocked(fsp.mkdir).mockResolvedValue(undefined);
    vi.mocked(fsp.copyFile).mockResolvedValue(undefined);

    const installer = new SkillInstaller("/workspace");
    await installer.fromLocal("./fixtures/cost_alarm");

    expect(fsp.copyFile).toHaveBeenCalledOnce();
  });

  it("throws when SKILL.md not found at local path", async () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);
    const installer = new SkillInstaller("/workspace");
    await expect(installer.fromLocal("./nonexistent")).rejects.toThrow(
      "SKILL.md not found",
    );
  });
});
