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
    log.engine.debug("[SkillHub] Schema initialized");
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
    log.engine.info("[SkillHub] Upserted skills", { count: skills.length });
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
      log.engine.debug("[SkillHub] FTS5 query failed, returning empty result", { query });
      return [];
    }
  }

  markInstalled(id: string): void {
    this.db
      .prepare(`UPDATE skills_catalog SET installed = 1, installed_at = ? WHERE id = ?`)
      .run(Date.now(), id);
    log.engine.debug("[SkillHub] Marked skill as installed", { id });
  }

  listInstalled(): InstalledSkill[] {
    return this.db
      .prepare(`SELECT * FROM skills_catalog WHERE installed = 1`)
      .all() as InstalledSkill[];
  }

  async refresh(registryUrl: string): Promise<number> {
    log.engine.info("[SkillHub] Refreshing registry", { url: registryUrl });
    const res = await fetch(registryUrl);
    if (!res.ok) {
      const err = new Error(`Registry fetch failed: ${res.status}`);
      log.engine.error("[SkillHub] Registry fetch failed", err, { status: res.status });
      throw err;
    }
    const data = (await res.json()) as { skills: CatalogSkill[] };
    if (!Array.isArray(data.skills)) {
      const err = new Error("Invalid registry format: expected { skills: [...] }");
      log.engine.error("[SkillHub] Invalid registry format", err);
      throw err;
    }
    this.upsertSkills(data.skills);
    log.engine.info("[SkillHub] Registry refreshed", { count: data.skills.length });
    return data.skills.length;
  }
}
