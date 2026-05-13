// __tests__/skills/skill-usage-sqlite.test.ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { MemoryDatabase } from '../../src/memory/db.js'
import { SkillTracker } from '../../src/skills/tracker.js'

let tmpDir: string
let db: MemoryDatabase

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), 'owl-skill-usage-'))
  db = new MemoryDatabase(tmpDir)
})

afterEach(() => {
  db.close()
  rmSync(tmpDir, { recursive: true, force: true })
})

describe('Q2: skill_usage SQLite persistence', () => {
  it('creates skill_usage table at v29', () => {
    const version = db.rawDb.pragma('user_version', { simple: true }) as number
    expect(version).toBe(33)
    const tables = (db.rawDb.prepare(
      `SELECT name FROM sqlite_master WHERE type='table' AND name='skill_usage'`
    ).all() as Array<{ name: string }>)
    expect(tables.length).toBe(1)
  })

  it('SkillTracker records selections to DB when db provided', () => {
    const tracker = new SkillTracker(tmpDir, db)
    tracker.recordSelection('web-research')
    tracker.recordSuccess('web-research', 1200)

    const stats = db.skillUsage.getStats('web-research')
    expect(stats).not.toBeNull()
    expect(stats!.selection_count).toBe(1)
    expect(stats!.success_count).toBe(1)
  })

  it('getUsageMultiplier returns > 1 after success', () => {
    const tracker = new SkillTracker(tmpDir, db)
    tracker.recordSelection('web-research')
    tracker.recordSuccess('web-research', 500)

    const multiplier = tracker.getUsageMultiplier('web-research')
    expect(multiplier).toBeGreaterThan(1.0)
  })

  it('falls back to JSON path when no db provided', () => {
    const tracker = new SkillTracker(tmpDir)
    tracker.recordSelection('local-skill')
    const multiplier = tracker.getUsageMultiplier('local-skill')
    expect(typeof multiplier).toBe('number')
  })
})
