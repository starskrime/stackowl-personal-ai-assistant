import { describe, it, expect, afterEach } from "vitest";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { rm } from "node:fs/promises";
import Database from "better-sqlite3";
import { SkillHub } from "../../src/skills/hub.js";

const DB_PATH = join(tmpdir(), `stackowl-hub-test-${Date.now()}.db`);

afterEach(async () => {
  await rm(DB_PATH, { force: true });
});

describe("SkillHub", () => {
  it("initializes the skills_catalog table", () => {
    const db = new Database(DB_PATH);
    const hub = new SkillHub(db);
    hub.initSchema();
    const tables = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table'")
      .all()
      .map((r: any) => r.name);
    expect(tables).toContain("skills_catalog");
    db.close();
  });

  it("upserts skills and searches via FTS5", () => {
    const db = new Database(DB_PATH);
    const hub = new SkillHub(db);
    hub.initSchema();
    hub.upsertSkills([
      {
        id: "git_commit",
        name: "git_commit",
        description: "Stage and commit files with conventional message",
        version: "1.0.0",
        author: "stackowl",
        homepage: null,
        registry_url: "https://example.com/git_commit.tar.gz",
      },
      {
        id: "web_research",
        name: "web_research",
        description: "Search the web and summarise results",
        version: "1.0.0",
        author: "stackowl",
        homepage: null,
        registry_url: "https://example.com/web_research.tar.gz",
      },
    ]);

    const results = hub.search("commit files");
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].name).toBe("git_commit");
    db.close();
  });

  it("marks a skill as installed", () => {
    const db = new Database(DB_PATH);
    const hub = new SkillHub(db);
    hub.initSchema();
    hub.upsertSkills([
      {
        id: "git_commit",
        name: "git_commit",
        description: "Stage and commit",
        version: "1.0.0",
        author: "stackowl",
        homepage: null,
        registry_url: "https://example.com/git_commit.tar.gz",
      },
    ]);

    hub.markInstalled("git_commit");
    const installed = hub.listInstalled();
    expect(installed).toHaveLength(1);
    expect(installed[0].name).toBe("git_commit");
    db.close();
  });

  it("returns empty array when search finds nothing", () => {
    const db = new Database(DB_PATH);
    const hub = new SkillHub(db);
    hub.initSchema();
    const results = hub.search("nonexistent_zxqwerty");
    expect(results).toHaveLength(0);
    db.close();
  });
});
