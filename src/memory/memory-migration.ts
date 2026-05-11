import { existsSync, readFileSync } from "node:fs";
import { log } from "../logger.js";
import type { MemoryDatabase } from "./db.js";
import type { FactCategory } from "./db.js";
import { SECTION_TO_CATEGORY } from "../tools/update-memory.js";

const SENTINEL_ENTITY = "migration:memory-md";

export async function migrateMemoryMd(
  db: MemoryDatabase,
  memoryMdPath: string,
): Promise<void> {
  // Idempotency check — skip if sentinel fact already exists
  const already = db.facts.getAllForUser().find((f) => f.entity === SENTINEL_ENTITY);
  if (already) {
    log.engine.debug("[MemoryMigration] Already migrated — skipping");
    return;
  }

  if (!existsSync(memoryMdPath)) {
    log.engine.debug("[MemoryMigration] No MEMORY.md found — skipping");
    markMigrated(db);
    return;
  }

  const raw = readFileSync(memoryMdPath, "utf-8");
  const lines = raw.split("\n");

  let currentCategory: FactCategory = "preference";
  let importedCount = 0;

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("#")) {
      const header = trimmed.replace(/^#+\s*/, "").toLowerCase();
      currentCategory = (SECTION_TO_CATEGORY[header] ?? "preference") as FactCategory;
      continue;
    }

    if (!trimmed || !trimmed.startsWith("-")) continue;

    const fact = trimmed.replace(/^-\s*/, "").trim();
    if (!fact) continue;

    db.facts.add({
      userId: "default",
      owlName: "default",
      fact,
      category: currentCategory,
      confidence: 0.9,
      source: "explicit",
    });
    importedCount++;
  }

  markMigrated(db);
  log.engine.info(`[MemoryMigration] Imported ${importedCount} facts from MEMORY.md`, {
    path: memoryMdPath,
    count: importedCount,
  });
}

function markMigrated(db: MemoryDatabase): void {
  db.facts.add({
    userId: "default",
    owlName: "default",
    fact: "MEMORY.md migration completed",
    entity: SENTINEL_ENTITY,
    category: "context",
    confidence: 1.0,
    source: "explicit",
  });
}
