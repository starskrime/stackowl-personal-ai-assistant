import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import Database from 'better-sqlite3';
import { applyMigrations } from '../../src/memory/db.js';

describe('schema v19 — clarification_asked column', () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(':memory:');
    applyMigrations(db);
  });
  afterEach(() => db.close());

  it('trajectories table has clarification_asked column defaulting to 0', () => {
    const info = db.prepare(
      `PRAGMA table_info(trajectories)`
    ).all() as Array<{ name: string; dflt_value: string | null }>;
    const col = info.find(c => c.name === 'clarification_asked');
    expect(col).toBeDefined();
    expect(col!.dflt_value).toBe('0');
  });

  it('schema version is at least 22', () => {
    const v = (db.pragma('user_version') as Array<{ user_version: number }>)[0].user_version;
    expect(v).toBeGreaterThanOrEqual(22);
  });
});
