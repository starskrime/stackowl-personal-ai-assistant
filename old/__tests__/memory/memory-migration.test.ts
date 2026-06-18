import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { migrateMemoryMd } from "../../src/memory/memory-migration.js";

let tmpDir: string;
let db: MemoryDatabase;
let memoryMdPath: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-migration-"));
  db = new MemoryDatabase(tmpDir);
  memoryMdPath = join(tmpDir, "MEMORY.md");
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("migrateMemoryMd", () => {
  it("imports bullet lines as facts in the correct category", async () => {
    writeFileSync(memoryMdPath,
      "# Preferences\n- Concise responses\n- TypeScript strict mode\n\n# Goals\n- Ship StackOwl v2\n"
    );

    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser();
    expect(facts.some((f) => f.fact === "Concise responses" && f.category === "preference")).toBe(true);
    expect(facts.some((f) => f.fact === "TypeScript strict mode" && f.category === "preference")).toBe(true);
    expect(facts.some((f) => f.fact === "Ship StackOwl v2" && f.category === "active_goal")).toBe(true);
  });

  it("is idempotent — running twice does not double-import facts", async () => {
    writeFileSync(memoryMdPath, "# Preferences\n- Concise responses\n");

    await migrateMemoryMd(db, memoryMdPath);
    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser().filter((f) => f.fact === "Concise responses");
    expect(facts).toHaveLength(1);
  });

  it("is a no-op when MEMORY.md does not exist", async () => {
    await migrateMemoryMd(db, join(tmpDir, "nonexistent.md"));
    const facts = db.facts.getAllForUser().filter((f) => f.entity !== "migration:memory-md");
    expect(facts).toHaveLength(0);
  });

  it("skips empty lines and section headers", async () => {
    writeFileSync(memoryMdPath, "# Preferences\n\n- Real fact\n\n# About me\n");
    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser().filter((f) => f.entity !== "migration:memory-md");
    expect(facts).toHaveLength(1);
    expect(facts[0].fact).toBe("Real fact");
  });
});
