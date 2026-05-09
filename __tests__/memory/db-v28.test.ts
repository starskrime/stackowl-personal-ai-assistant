// __tests__/memory/db-v28.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import Database from "better-sqlite3"
import { applyV28Element17Migration } from "../../src/memory/db.js"

describe("v28 Element17 migration", () => {
  let db: Database.Database

  beforeEach(() => { db = new Database(":memory:") })
  afterEach(() => { db.close() })

  it("creates owl_quality_metrics table", () => {
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_quality_metrics'"
    ).all()
    expect(tables).toHaveLength(1)
  })

  it("creates owl_pins table with composite PK", () => {
    applyV28Element17Migration(db)
    const cols = db.prepare("PRAGMA table_info(owl_pins)").all() as any[]
    expect(cols.map((c: any) => c.name)).toContain("channel_id")
    expect(cols.map((c: any) => c.name)).toContain("user_id")
  })

  it("creates owl_recurring_jobs table", () => {
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_recurring_jobs'"
    ).all()
    expect(tables).toHaveLength(1)
  })

  it("drops owls table", () => {
    db.exec(`CREATE TABLE IF NOT EXISTS owls (
      id TEXT PRIMARY KEY, owner_id TEXT, name TEXT, specialization TEXT,
      personality_prompt TEXT, routing_rules TEXT, dna TEXT, is_main_owl INTEGER,
      created_at TEXT, updated_at TEXT
    )`)
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owls'"
    ).all()
    expect(tables).toHaveLength(0)
  })

  it("is idempotent", () => {
    applyV28Element17Migration(db)
    expect(() => applyV28Element17Migration(db)).not.toThrow()
  })
})
