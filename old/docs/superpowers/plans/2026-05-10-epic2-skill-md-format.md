# Epic 2: SKILL.md Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface user-installed SKILL.md skills from `~/.stackowl/skills/`, add progressive disclosure so only name+description hit the system prompt by default, ship CreateSkillTool so the LLM can author new skills, and build SkillHub with a local SQLite FTS5 cache backed by a remote registry.

**Architecture:** `SkillsLoader` already loads SKILL.md files from configured directories and `SkillsRegistry.loadFromDirectory()` is fully functional with 149 defaults. The four additions are: (1) auto-include `~/.stackowl/skills/` at startup, (2) a `formatSkillsHeader()` method on `SkillsRegistry` that emits only name+description for system-prompt injection (the existing `formatForContext` emits full instructions on demand), (3) a new `CreateSkillTool` that writes SKILL.md files and refreshes the registry, and (4) `SkillHub` — a new `src/skills/hub.ts` that stores a remote registry snapshot in the existing `MemoryDatabase` SQLite via a new `skills_catalog` table with FTS5.

**Tech Stack:** TypeScript, Node 22, `better-sqlite3` (already in use), `gray-matter` (already in package.json), `SkillsRegistry`/`SkillsLoader` (existing), Vitest.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/skills/registry.ts` | Modify | Add `formatSkillsHeader()` for Tier-1 (name+desc) system prompt injection |
| `src/skills/loader.ts` | Modify | Add user skills dir (`~/.stackowl/skills/`) to load list at startup |
| `src/tools/create-skill.ts` | Create | CreateSkillTool — writes SKILL.md to user dir, refreshes registry |
| `src/skills/hub.ts` | Create | SkillHub — remote registry → local SQLite FTS5 cache |
| `src/memory/db.ts` | Modify | Add `skills_catalog` + FTS5 virtual table to schema (bump SCHEMA_VERSION) |
| `__tests__/skills/registry-header.test.ts` | Create | Unit tests for `formatSkillsHeader()` |
| `__tests__/skills/hub.test.ts` | Create | Unit tests for SkillHub search + install |
| `__tests__/tools/create-skill.test.ts` | Create | Unit tests for CreateSkillTool |

---

## Task 1: Progressive disclosure — formatSkillsHeader()

**Files:**
- Modify: `src/skills/registry.ts`
- Create: `__tests__/skills/registry-header.test.ts`

- [ ] **Step 1.1: Write the failing test**

```typescript
// __tests__/skills/registry-header.test.ts
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
    expect(header).toContain("<description>Stage changed files and commit</description>");
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
});
```

- [ ] **Step 1.2: Run test to confirm it fails**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npx vitest run __tests__/skills/registry-header.test.ts 2>&1 | tail -20
```

Expected: FAIL — `registry.formatSkillsHeader` is not a function.

- [ ] **Step 1.3: Add formatSkillsHeader() to SkillsRegistry**

In `src/skills/registry.ts`, add this method after `formatForContextSingle()`:

```typescript
  /**
   * Tier-1 system prompt injection: name + description only.
   * Full instructions are loaded on demand when a skill triggers.
   * Keeps the base system prompt lean (~50 words per skill).
   */
  formatSkillsHeader(skills: Skill[]): string {
    if (skills.length === 0) return "";
    const lines: string[] = ["<available_skills>"];
    for (const skill of skills) {
      lines.push(`  <skill>`);
      lines.push(`    <name>${skill.name}</name>`);
      lines.push(`    <description>${skill.description}</description>`);
      lines.push(`  </skill>`);
    }
    lines.push("</available_skills>");
    return lines.join("\n");
  }
```

- [ ] **Step 1.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/skills/registry-header.test.ts 2>&1 | tail -20
```

Expected: PASS — 3 tests passing.

- [ ] **Step 1.5: Commit**

```bash
git add src/skills/registry.ts __tests__/skills/registry-header.test.ts
git commit -m "feat(skills): add formatSkillsHeader() for Tier-1 progressive disclosure"
```

---

## Task 2: Load user skills directory at startup

**Files:**
- Modify: `src/skills/loader.ts`
- Modify: wherever `SkillsLoader.load()` is called (find at step 2.1)

- [ ] **Step 2.1: Find startup call site**

```bash
grep -rn "SkillsLoader\|skillsLoader\|\.load(" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|spec\|__tests__" | head -20
```

- [ ] **Step 2.2: Add user skills path helper to loader**

In `src/skills/loader.ts`, add this import at the top:

```typescript
import { homedir } from "node:os";
import { join } from "node:path";
```

Add this static method to `SkillsLoader`:

```typescript
  /**
   * Returns the user's private skills directory, creating it if needed.
   * Parallel to OpenClaw's ~/.openclaw/skills/ convention.
   */
  static userSkillsDir(): string {
    const dir = join(homedir(), ".stackowl", "skills");
    return dir;
  }
```

- [ ] **Step 2.3: Include user dir in load call**

At the startup call site found in Step 2.1, change the `directories` array to prepend the user dir:

```typescript
// Before (example — use actual startup file):
await skillsLoader.load({ directories: [defaultSkillsDir] });

// After:
import { SkillsLoader } from "./skills/loader.js";
// ...
await skillsLoader.load({
  directories: [
    SkillsLoader.userSkillsDir(),  // user's ~/.stackowl/skills/ — highest priority
    defaultSkillsDir,              // bundled defaults
  ],
  watch: true,
});
```

If `~/.stackowl/skills/` doesn't exist yet, `SkillsLoader.load()` already skips missing directories gracefully (see `existsSync` guard in loader). No mkdir needed here.

- [ ] **Step 2.4: Smoke test — verify both dirs scan on startup**

```bash
mkdir -p ~/.stackowl/skills/hello_world
cat > ~/.stackowl/skills/hello_world/SKILL.md <<'EOF'
---
name: hello_world
description: A test skill to verify user skills dir is loaded
---
Say "hello world".
EOF
npm run dev 2>&1 | grep -i "skill" | head -10
```

Expected: `[SkillsLoader] Loaded N skills from /home/<user>/.stackowl/skills` and `[SkillsLoader] Loaded 149 skills from ...defaults`.

- [ ] **Step 2.5: Clean up test skill and commit**

```bash
rm -rf ~/.stackowl/skills/hello_world
git add src/skills/loader.ts  # and startup file
git commit -m "feat(skills): auto-load user skills from ~/.stackowl/skills/ at startup"
```

---

## Task 3: CreateSkillTool

**Files:**
- Create: `src/tools/create-skill.ts`
- Create: `__tests__/tools/create-skill.test.ts`
- Modify: `src/tools/registry.ts` (or wherever tools are registered — see step 3.1)

- [ ] **Step 3.1: Find where tools are registered**

```bash
grep -rn "registerTool\|ToolRegistry\|new.*Tool(" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -20
```

- [ ] **Step 3.2: Write the failing test**

```typescript
// __tests__/tools/create-skill.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
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

  it("rejects names that are not hyphen-case alphanumeric", async () => {
    const tool = await makeTool();
    await expect(
      tool.execute({ name: "My Skill!", description: "bad", instructions: "x" }),
    ).rejects.toThrow(/invalid name/i);
  });

  it("rejects description longer than 64 characters", async () => {
    const tool = await makeTool();
    await expect(
      tool.execute({ name: "fine_name", description: "a".repeat(65), instructions: "x" }),
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
```

- [ ] **Step 3.3: Run test to confirm it fails**

```bash
npx vitest run __tests__/tools/create-skill.test.ts 2>&1 | tail -20
```

Expected: FAIL — `CreateSkillTool` module does not exist.

- [ ] **Step 3.4: Implement CreateSkillTool**

Create `src/tools/create-skill.ts`:

```typescript
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";

const NAME_RE = /^[a-z][a-z0-9_]*$/;

export interface CreateSkillInput {
  /** Hyphen/underscore-case name, e.g. "git_commit" */
  name: string;
  /** Short description — under 64 chars. Becomes the trigger hint in the system prompt. */
  description: string;
  /** Markdown instructions for the LLM — the SKILL.md body. */
  instructions: string;
}

export class CreateSkillTool {
  readonly name = "create_skill";
  readonly description =
    "Create a new SKILL.md skill file. " +
    "Use when the user wants to teach you a new repeatable behaviour or workflow.";

  constructor(private skillsDir?: string) {}

  private resolveDir(): string {
    return this.skillsDir ?? join(homedir(), ".stackowl", "skills");
  }

  async execute(input: CreateSkillInput): Promise<string> {
    log.tool.debug("create_skill.execute: entry", { name: input.name });

    if (!NAME_RE.test(input.name)) {
      throw new Error(
        `Invalid name "${input.name}". Use lowercase letters, digits, and underscores only.`,
      );
    }
    if (input.description.length > 64) {
      throw new Error(
        `Description too long (${input.description.length} chars). Keep it under 64.`,
      );
    }
    if (!input.instructions || input.instructions.trim().length < 10) {
      throw new Error("Instructions must be at least 10 characters.");
    }

    const skillDir = join(this.resolveDir(), input.name);
    const skillPath = join(skillDir, "SKILL.md");

    await mkdir(skillDir, { recursive: true });

    const content = [
      "---",
      `name: ${input.name}`,
      `description: ${input.description}`,
      "---",
      "",
      input.instructions.trim(),
      "",
    ].join("\n");

    await writeFile(skillPath, content, "utf-8");

    log.tool.info("create_skill.execute: skill created", { path: skillPath });
    return `Skill "${input.name}" created at ${skillPath}. It will be available on next message.`;
  }
}
```

- [ ] **Step 3.5: Run test to confirm it passes**

```bash
npx vitest run __tests__/tools/create-skill.test.ts 2>&1 | tail -20
```

Expected: PASS — 4 tests passing.

- [ ] **Step 3.6: Register the tool**

In the tool registry file identified in Step 3.1, add:

```typescript
import { CreateSkillTool } from "./create-skill.js";
// In the registry setup:
registry.register(new CreateSkillTool());
```

- [ ] **Step 3.7: Commit**

```bash
git add src/tools/create-skill.ts __tests__/tools/create-skill.test.ts  # + registry file
git commit -m "feat(skills): CreateSkillTool — LLM can author SKILL.md files"
```

---

## Task 4: SkillHub — SQLite FTS5 marketplace cache

**Files:**
- Modify: `src/memory/db.ts`
- Create: `src/skills/hub.ts`
- Create: `__tests__/skills/hub.test.ts`

- [ ] **Step 4.1: Write the failing tests**

```typescript
// __tests__/skills/hub.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { rm } from "node:fs/promises";
import Database from "better-sqlite3";
import { SkillHub } from "../../src/skills/hub.js";

const DB_PATH = join(tmpdir(), `stackowl-hub-test-${Date.now()}.db`);

function makeHub() {
  const db = new Database(DB_PATH);
  return new SkillHub(db);
}

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

  it("upserts skills from registry data and searches via FTS5", () => {
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
```

- [ ] **Step 4.2: Run tests to confirm they fail**

```bash
npx vitest run __tests__/skills/hub.test.ts 2>&1 | tail -20
```

Expected: FAIL — `SkillHub` module does not exist.

- [ ] **Step 4.3: Add skills_catalog to MemoryDatabase schema**

In `src/memory/db.ts`, bump `SCHEMA_VERSION` by 1 and add the schema inside the `if (currentVersion < NEW_VERSION)` migration block (follow the existing migration pattern in the file). If there is a `createTables()` or initial schema string, add these tables there as well:

```sql
CREATE TABLE IF NOT EXISTS skills_catalog (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL,
  version      TEXT NOT NULL,
  author       TEXT,
  homepage     TEXT,
  registry_url TEXT NOT NULL,
  installed    INTEGER DEFAULT 0,
  installed_at INTEGER,
  last_synced  INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS skills_catalog_fts
  USING fts5(name, description, content='skills_catalog', content_rowid='rowid');

CREATE TRIGGER IF NOT EXISTS skills_catalog_ai
  AFTER INSERT ON skills_catalog BEGIN
    INSERT INTO skills_catalog_fts(rowid, name, description)
    VALUES (new.rowid, new.name, new.description);
  END;

CREATE TRIGGER IF NOT EXISTS skills_catalog_ad
  AFTER DELETE ON skills_catalog BEGIN
    INSERT INTO skills_catalog_fts(skills_catalog_fts, rowid, name, description)
    VALUES ('delete', old.rowid, old.name, old.description);
  END;

CREATE TRIGGER IF NOT EXISTS skills_catalog_au
  AFTER UPDATE ON skills_catalog BEGIN
    INSERT INTO skills_catalog_fts(skills_catalog_fts, rowid, name, description)
    VALUES ('delete', old.rowid, old.name, old.description);
    INSERT INTO skills_catalog_fts(rowid, name, description)
    VALUES (new.rowid, new.name, new.description);
  END;
```

- [ ] **Step 4.4: Implement SkillHub**

Create `src/skills/hub.ts`:

```typescript
import type Database from "better-sqlite3";
import { log } from "../logger.js";

export interface CatalogSkill {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string | null;
  homepage: string | null;
  registry_url: string;
}

export interface InstalledSkill extends CatalogSkill {
  installed: number;
  installed_at: number | null;
  last_synced: number;
}

export class SkillHub {
  constructor(private db: Database.Database) {}

  initSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS skills_catalog (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL,
        version      TEXT NOT NULL,
        author       TEXT,
        homepage     TEXT,
        registry_url TEXT NOT NULL,
        installed    INTEGER DEFAULT 0,
        installed_at INTEGER,
        last_synced  INTEGER NOT NULL DEFAULT 0
      );
      CREATE VIRTUAL TABLE IF NOT EXISTS skills_catalog_fts
        USING fts5(name, description, content='skills_catalog', content_rowid='rowid');
      CREATE TRIGGER IF NOT EXISTS skills_catalog_ai
        AFTER INSERT ON skills_catalog BEGIN
          INSERT INTO skills_catalog_fts(rowid, name, description)
          VALUES (new.rowid, new.name, new.description);
        END;
      CREATE TRIGGER IF NOT EXISTS skills_catalog_ad
        AFTER DELETE ON skills_catalog BEGIN
          INSERT INTO skills_catalog_fts(skills_catalog_fts, rowid, name, description)
          VALUES ('delete', old.rowid, old.name, old.description);
        END;
      CREATE TRIGGER IF NOT EXISTS skills_catalog_au
        AFTER UPDATE ON skills_catalog BEGIN
          INSERT INTO skills_catalog_fts(skills_catalog_fts, rowid, name, description)
          VALUES ('delete', old.rowid, old.name, old.description);
          INSERT INTO skills_catalog_fts(rowid, name, description)
          VALUES (new.rowid, new.name, new.description);
        END;
    `);
  }

  upsertSkills(skills: CatalogSkill[]): void {
    const now = Date.now();
    const stmt = this.db.prepare(`
      INSERT INTO skills_catalog (id, name, description, version, author, homepage, registry_url, last_synced)
      VALUES (@id, @name, @description, @version, @author, @homepage, @registry_url, @last_synced)
      ON CONFLICT(id) DO UPDATE SET
        name         = excluded.name,
        description  = excluded.description,
        version      = excluded.version,
        author       = excluded.author,
        homepage     = excluded.homepage,
        registry_url = excluded.registry_url,
        last_synced  = excluded.last_synced
    `);
    const insertMany = this.db.transaction((rows: CatalogSkill[]) => {
      for (const row of rows) {
        stmt.run({ ...row, last_synced: now });
      }
    });
    insertMany(skills);
    log.engine.info(`[SkillHub] Upserted ${skills.length} skills`);
  }

  search(query: string, limit = 10): InstalledSkill[] {
    if (!query.trim()) return [];
    try {
      return this.db
        .prepare(
          `SELECT c.* FROM skills_catalog c
           JOIN skills_catalog_fts fts ON c.rowid = fts.rowid
           WHERE skills_catalog_fts MATCH ?
           ORDER BY rank LIMIT ?`,
        )
        .all(query, limit) as InstalledSkill[];
    } catch {
      return [];
    }
  }

  markInstalled(id: string): void {
    this.db
      .prepare(
        `UPDATE skills_catalog SET installed = 1, installed_at = ? WHERE id = ?`,
      )
      .run(Date.now(), id);
  }

  listInstalled(): InstalledSkill[] {
    return this.db
      .prepare(`SELECT * FROM skills_catalog WHERE installed = 1`)
      .all() as InstalledSkill[];
  }

  async refresh(registryUrl: string): Promise<number> {
    log.engine.info("[SkillHub] Refreshing registry...", { url: registryUrl });
    const res = await fetch(registryUrl);
    if (!res.ok) {
      throw new Error(`Registry fetch failed: ${res.status}`);
    }
    const data = (await res.json()) as { skills: CatalogSkill[] };
    if (!Array.isArray(data.skills)) {
      throw new Error("Invalid registry format: expected { skills: [...] }");
    }
    this.upsertSkills(data.skills);
    log.engine.info(`[SkillHub] Refreshed ${data.skills.length} skills`);
    return data.skills.length;
  }
}
```

- [ ] **Step 4.5: Run tests to confirm they pass**

```bash
npx vitest run __tests__/skills/hub.test.ts 2>&1 | tail -20
```

Expected: PASS — 4 tests passing.

- [ ] **Step 4.6: Run full test suite for regressions**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all previously-passing tests still pass.

- [ ] **Step 4.7: Commit**

```bash
git add src/skills/hub.ts src/memory/db.ts __tests__/skills/hub.test.ts
git commit -m "feat(skills): SkillHub — SQLite FTS5 marketplace cache with remote registry sync"
```
