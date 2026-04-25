import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillsMigrator } from "../src/skills/migrator.js";
import * as fsp from "node:fs/promises";
import * as fs from "node:fs";

vi.mock("node:fs/promises");
vi.mock("node:fs");
vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

describe("SkillsMigrator", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("copies INSTINCT.md to SKILL.md and logs the migration", async () => {
    const existsSyncMock = vi.mocked(fs.existsSync);
    const readdirMock = vi.mocked(fsp.readdir);
    const mkdirMock = vi.mocked(fsp.mkdir);
    const copyFileMock = vi.mocked(fsp.copyFile);

    existsSyncMock.mockImplementation((p) => {
      const path = String(p);
      return path.includes("instincts") || path.includes("skills") || path.includes("INSTINCT.md");
    });

    readdirMock.mockResolvedValue([
      { name: "cost-alarm", isDirectory: () => true, isFile: () => false } as any,
    ]);

    mkdirMock.mockResolvedValue(undefined);
    copyFileMock.mockResolvedValue(undefined);

    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();

    expect(count).toBe(1);
    expect(copyFileMock).toHaveBeenCalledOnce();
    const [src, dest] = copyFileMock.mock.calls[0];
    expect(String(src)).toContain("INSTINCT.md");
    expect(String(dest)).toContain("SKILL.md");
  });

  it("returns 0 when instincts directory does not exist", async () => {
    vi.mocked(fs.existsSync).mockReturnValue(false);
    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();
    expect(count).toBe(0);
  });

  it("skips subdirectories that have no INSTINCT.md", async () => {
    vi.mocked(fs.existsSync).mockImplementation((p) => {
      const path = String(p);
      // instincts dir exists but no INSTINCT.md files
      return path.endsWith("instincts") && !path.includes("INSTINCT.md");
    });
    vi.mocked(fsp.readdir).mockResolvedValue([
      { name: "empty-dir", isDirectory: () => true, isFile: () => false } as any,
    ]);
    const migrator = new SkillsMigrator("/workspace");
    const count = await migrator.migrate();
    expect(count).toBe(0);
  });
});
