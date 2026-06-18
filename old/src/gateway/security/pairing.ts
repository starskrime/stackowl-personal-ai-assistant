import type Database from "better-sqlite3";
import { randomBytes } from "node:crypto";
import { log } from "../../logger.js";

function generateCode(): string {
  return randomBytes(3).toString("hex").toUpperCase().slice(0, 6);
}

export class PairingService {
  constructor(private db: Database.Database) {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS pairing_allowlist (
        channel    TEXT NOT NULL,
        sender_id  TEXT NOT NULL,
        approved   INTEGER DEFAULT 0,
        code       TEXT,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()),
        PRIMARY KEY (channel, sender_id)
      );
    `);
  }

  isAuthorized(channel: string, senderId: string): boolean {
    const row = this.db
      .prepare(`SELECT approved FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .get(channel, senderId) as { approved: number } | undefined;
    return row?.approved === 1;
  }

  challenge(channel: string, senderId: string): string {
    const existing = this.db
      .prepare(`SELECT code FROM pairing_allowlist WHERE channel = ? AND sender_id = ? AND approved = 0`)
      .get(channel, senderId) as { code: string } | undefined;

    if (existing?.code) return existing.code;

    const code = generateCode();
    this.db
      .prepare(
        `INSERT INTO pairing_allowlist (channel, sender_id, approved, code)
         VALUES (?, ?, 0, ?)
         ON CONFLICT(channel, sender_id) DO UPDATE SET code = excluded.code, approved = 0`,
      )
      .run(channel, senderId, code);

    log.engine.info("[PairingService] Challenge issued", { channel, senderId });
    return code;
  }

  approve(channel: string, senderId: string, code: string): boolean {
    const row = this.db
      .prepare(`SELECT code FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .get(channel, senderId) as { code: string } | undefined;

    if (!row || row.code !== code) return false;

    this.db
      .prepare(`UPDATE pairing_allowlist SET approved = 1, code = NULL WHERE channel = ? AND sender_id = ?`)
      .run(channel, senderId);

    log.engine.info("[PairingService] Sender approved", { channel, senderId });
    return true;
  }

  revoke(channel: string, senderId: string): void {
    this.db
      .prepare(`DELETE FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .run(channel, senderId);
  }
}
