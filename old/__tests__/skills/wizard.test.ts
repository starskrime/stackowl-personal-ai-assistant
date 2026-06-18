import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillInstallWizard } from "../../src/skills/wizard.js";

const mockClawHub = {
  search: vi.fn(),
  install: vi.fn(),
};

vi.mock("../../src/skills/installer.js", () => ({
  SkillInstaller: vi.fn().mockImplementation(() => ({
    fromGitHub: vi.fn().mockResolvedValue(undefined),
    fromLocal: vi.fn().mockResolvedValue(undefined),
  })),
}));

describe("SkillInstallWizard", () => {
  let wizard: SkillInstallWizard;

  beforeEach(() => {
    wizard = new SkillInstallWizard("/workspace/skills", mockClawHub as any);
    vi.clearAllMocks();
  });

  it("start() returns source menu with 3-button keyboard", () => {
    const res = wizard.start();
    expect(res.done).toBe(false);
    expect(res.text).toContain("Choose a source");
    expect(res.inlineKeyboard).toBeDefined();
    expect(res.inlineKeyboard![0]).toHaveLength(3);
  });

  it("step('/cancel') exits immediately", async () => {
    const res = await wizard.step("/cancel");
    expect(res.done).toBe(true);
    expect(res.text).toBe("Cancelled.");
  });

  it("step('1') transitions to search_clawhub", async () => {
    const res = await wizard.step("1");
    expect(res.done).toBe(false);
    expect(res.text).toContain("keyword");
    expect(res.inlineKeyboard).toBeUndefined();
  });

  it("step('wiz:github') transitions to enter_github", async () => {
    const res = await wizard.step("wiz:github");
    expect(res.done).toBe(false);
    expect(res.text).toContain("GitHub path");
  });

  it("step('wiz:local') transitions to enter_local", async () => {
    const res = await wizard.step("wiz:local");
    expect(res.done).toBe(false);
    expect(res.text).toContain("local path");
  });

  it("invalid source input re-prompts with keyboard", async () => {
    const res = await wizard.step("99");
    expect(res.done).toBe(false);
    expect(res.text).toContain("1, 2, or 3");
    expect(res.inlineKeyboard).toBeDefined();
  });

  it("clawhub search returns results list with keyboard", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [
        { slug: "git_branch", name: "git_branch", description: "Manage branches", stars: 5, downloads: 100, tags: [], author: "test", latestVersion: "1.0", updatedAt: "" },
      ],
      total: 1,
    });
    await wizard.step("1");
    const res = await wizard.step("git");
    expect(res.done).toBe(false);
    expect(res.text).toContain("git_branch");
    expect(res.inlineKeyboard).toBeDefined();
  });

  it("clawhub search with 0 results re-prompts same step", async () => {
    mockClawHub.search.mockResolvedValue({ skills: [], total: 0 });
    await wizard.step("1");
    const res = await wizard.step("zzz");
    expect(res.done).toBe(false);
    expect(res.text).toContain("No skills found");
  });

  it("clawhub search error shows actual error and re-prompts", async () => {
    mockClawHub.search.mockRejectedValue(new Error("Network error"));
    await wizard.step("1");
    const res = await wizard.step("git");
    expect(res.done).toBe(false);
    expect(res.text).toContain("Network error");
  });

  it("picking by number installs skill and returns done", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [{ slug: "git_branch", name: "git_branch", description: "Manage branches", stars: 5, downloads: 100, tags: [], author: "test", latestVersion: "1.0", updatedAt: "" }],
      total: 1,
    });
    mockClawHub.install.mockResolvedValue(true);
    await wizard.step("1");
    await wizard.step("git");
    const res = await wizard.step("1");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
    expect(mockClawHub.install).toHaveBeenCalledWith("git_branch", "/workspace/skills");
  });

  it("picking by Telegram callback data installs skill", async () => {
    mockClawHub.search.mockResolvedValue({
      skills: [{ slug: "git_branch", name: "git_branch", description: "", stars: 0, downloads: 0, tags: [], author: "", latestVersion: "1.0", updatedAt: "" }],
      total: 1,
    });
    mockClawHub.install.mockResolvedValue(true);
    await wizard.step("1");
    await wizard.step("git");
    const res = await wizard.step("wiz:pick:git_branch");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });

  it("github install success", async () => {
    await wizard.step("2");
    const res = await wizard.step("github:myuser/myrepo/my-skill");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });

  it("github install with too-short path re-prompts", async () => {
    await wizard.step("2");
    const res = await wizard.step("notavalidpath");
    expect(res.done).toBe(false);
    expect(res.text).toContain("Invalid GitHub path");
  });

  it("local install success", async () => {
    await wizard.step("3");
    const res = await wizard.step("./my-skill");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
  });

  it("entering a slug (user/skill) in search step installs directly", async () => {
    mockClawHub.install.mockResolvedValue(true);
    await wizard.step("1"); // choose clawhub
    const res = await wizard.step("chenghaifeng08-creator/trading-automaton");
    expect(res.done).toBe(true);
    expect(res.text).toContain("✓ Installed");
    expect(mockClawHub.install).toHaveBeenCalledWith(
      "chenghaifeng08-creator/trading-automaton",
      "/workspace/skills",
    );
    // search should NOT have been called
    expect(mockClawHub.search).not.toHaveBeenCalled();
  });

  it("search API error shows actual error and re-prompts (done: false)", async () => {
    mockClawHub.search.mockRejectedValue(new Error("Search failed: 404 Not Found"));
    await wizard.step("1");
    const res = await wizard.step("trading");
    expect(res.done).toBe(false);
    expect(res.text).not.toContain("unavailable");
    expect(res.text).toContain("404");
  });
});
