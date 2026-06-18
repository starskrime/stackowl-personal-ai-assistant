import type { MemoryDatabase } from "../memory/db.js";
import type {
  Session,
  SessionMessage,
  SessionStatus,
  MessageDirection,
} from "./types.js";

interface SessionRow {
  id: string;
  parent_id: string | null;
  status: SessionStatus;
  prompt: string;
  history_json: string | null;
  result: string | null;
  error: string | null;
  metadata: string | null;
  created_at: string;
  updated_at: string;
  terminated_at: string | null;
}

interface MessageRow {
  id: number;
  session_id: string;
  direction: MessageDirection;
  content: string;
  created_at: string;
  consumed_at: string | null;
}

function rowToSession(r: SessionRow): Session {
  return {
    id: r.id,
    parentId: r.parent_id,
    status: r.status,
    prompt: r.prompt,
    history: r.history_json ? JSON.parse(r.history_json) : [],
    result: r.result ?? undefined,
    error: r.error ?? undefined,
    metadata: r.metadata ? JSON.parse(r.metadata) : {},
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    terminatedAt: r.terminated_at ?? undefined,
  };
}

function rowToMessage(r: MessageRow): SessionMessage {
  return {
    id: r.id,
    sessionId: r.session_id,
    direction: r.direction,
    content: r.content,
    createdAt: r.created_at,
    consumedAt: r.consumed_at ?? undefined,
  };
}

export class SessionStore {
  constructor(private readonly db: MemoryDatabase) {}

  create(session: Session): void {
    this.db.rawDb
      .prepare(
        `
      INSERT OR REPLACE INTO sessions
      (id, parent_id, status, prompt, history_json, result, error, metadata,
       created_at, updated_at, terminated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `
      )
      .run(
        session.id,
        session.parentId,
        session.status,
        session.prompt,
        JSON.stringify(session.history ?? []),
        session.result ?? null,
        session.error ?? null,
        JSON.stringify(session.metadata ?? {}),
        session.createdAt,
        session.updatedAt,
        session.terminatedAt ?? null
      );
  }

  update(id: string, patch: Partial<Session>): void {
    const existing = this.findOne(id);
    if (!existing) return;
    const next: Session = {
      ...existing,
      ...patch,
      metadata: { ...existing.metadata, ...(patch.metadata ?? {}) },
      updatedAt: new Date().toISOString(),
    };
    this.create(next);
  }

  findOne(id: string): Session | null {
    const row = this.db.rawDb
      .prepare("SELECT * FROM sessions WHERE id = ?")
      .get(id) as SessionRow | undefined;
    return row ? rowToSession(row) : null;
  }

  list(filter?: {
    status?: SessionStatus;
    parentId?: string;
    limit?: number;
  }): Session[] {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (filter?.status) {
      clauses.push("status = ?");
      params.push(filter.status);
    }
    if (filter?.parentId) {
      clauses.push("parent_id = ?");
      params.push(filter.parentId);
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const limit = Math.min(filter?.limit ?? 200, 1000);
    const rows = this.db.rawDb
      .prepare(
        `SELECT * FROM sessions ${where} ORDER BY updated_at DESC LIMIT ?`
      )
      .all(...params, limit) as SessionRow[];
    return rows.map(rowToSession);
  }

  appendMessage(
    sessionId: string,
    direction: MessageDirection,
    content: string
  ): SessionMessage {
    const result = this.db.rawDb
      .prepare(
        `
      INSERT INTO session_messages (session_id, direction, content)
      VALUES (?, ?, ?)
    `
      )
      .run(sessionId, direction, content);
    const id = Number(result.lastInsertRowid);
    const row = this.db.rawDb
      .prepare("SELECT * FROM session_messages WHERE id = ?")
      .get(id) as MessageRow;
    return rowToMessage(row);
  }

  pendingMessages(
    sessionId: string,
    direction?: MessageDirection
  ): SessionMessage[] {
    const sql = direction
      ? "SELECT * FROM session_messages WHERE session_id = ? AND direction = ? AND consumed_at IS NULL ORDER BY id"
      : "SELECT * FROM session_messages WHERE session_id = ? AND consumed_at IS NULL ORDER BY id";
    const params = direction ? [sessionId, direction] : [sessionId];
    const rows = this.db.rawDb
      .prepare(sql)
      .all(...params) as MessageRow[];
    return rows.map(rowToMessage);
  }

  markConsumed(messageId: number): void {
    this.db.rawDb
      .prepare("UPDATE session_messages SET consumed_at = datetime('now') WHERE id = ?")
      .run(messageId);
  }
}
