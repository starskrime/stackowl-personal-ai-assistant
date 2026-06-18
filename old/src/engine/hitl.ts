import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type { HitlRequest, HitlResponse, HitlChannel, TaskLedger } from "./types.js";

interface StoredCheckpoint {
  id: string;
  sessionId: string;
  ledgerId: string;
  requestKind: HitlRequest["kind"];
  memo: HitlRequest["memo"];
  ledgerSnapshot: TaskLedger | null;
  pendingAction: string;
  status: "waiting" | "resolved" | "expired";
  response?: HitlResponse;
  createdAt: string;
  resolvedAt?: string;
  expiresAt: string;
}

export class HitlCheckpointStore {
  constructor(private readonly db: MemoryDatabase) {}

  async create(
    sessionId: string,
    ledgerId: string,
    request: HitlRequest,
    ttlMinutes: number,
  ): Promise<string> {
    const id = uuidv4();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + ttlMinutes * 60_000).toISOString();
    this.db.rawDb.prepare(`
      INSERT INTO hitl_checkpoints
        (id, session_id, ledger_id, pending_action, request_kind, memo_json,
         status, created_at, expires_at)
      VALUES (?,?,?,?,?,?,?,?,?)
    `).run(
      id, sessionId, ledgerId, request.pendingAction,
      request.kind, JSON.stringify({ memo: request.memo, ledger: request.ledgerSnapshot }),
      "waiting", now.toISOString(), expiresAt,
    );
    return id;
  }

  async resolve(id: string, response: HitlResponse): Promise<void> {
    this.db.rawDb.prepare(`
      UPDATE hitl_checkpoints
      SET status = 'resolved', response_json = ?, resolved_at = ?
      WHERE id = ?
    `).run(JSON.stringify(response), new Date().toISOString(), id);
  }

  async load(id: string): Promise<StoredCheckpoint | null> {
    const row = this.db.rawDb.prepare(
      "SELECT * FROM hitl_checkpoints WHERE id = ?"
    ).get(id) as Record<string, unknown> | undefined;
    if (!row) return null;
    return this._parse(row);
  }

  async getWaiting(sessionId: string): Promise<StoredCheckpoint[]> {
    const rows = this.db.rawDb.prepare(
      "SELECT * FROM hitl_checkpoints WHERE session_id = ? AND status = 'waiting' AND expires_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now') ORDER BY created_at DESC"
    ).all(sessionId) as Record<string, unknown>[];
    return rows.map((r) => this._parse(r));
  }

  private _parse(row: Record<string, unknown>): StoredCheckpoint {
    const memoData = JSON.parse((row["memo_json"] as string | null) ?? "{}") as {
      memo?: HitlRequest["memo"];
      ledger?: TaskLedger;
    };
    return {
      id: row["id"] as string,
      sessionId: row["session_id"] as string,
      ledgerId: row["ledger_id"] as string,
      requestKind: row["request_kind"] as HitlRequest["kind"],
      memo: (memoData.memo ?? {}) as HitlRequest["memo"],
      ledgerSnapshot: memoData.ledger ?? null,
      pendingAction: row["pending_action"] as string,
      status: row["status"] as "waiting" | "resolved" | "expired",
      response: row["response_json"]
        ? (JSON.parse(row["response_json"] as string) as HitlResponse)
        : undefined,
      createdAt: row["created_at"] as string,
      resolvedAt: (row["resolved_at"] as string | null) ?? undefined,
      expiresAt: row["expires_at"] as string,
    };
  }
}

export class CliHitlChannel implements HitlChannel {
  async pause(request: HitlRequest): Promise<HitlResponse> {
    const { memo } = request;
    console.log(`\n[HITL] Owl needs your input:`);
    console.log(`  What I did: ${memo.whatIDid}`);
    console.log(`  What I need: ${memo.whatINeed}`);
    if (memo.options) {
      memo.options.forEach((opt, i) => console.log(`  ${i + 1}. ${opt}`));
    }
    console.log(`  [Auto-approving in CLI mode]`);
    return { approved: true, timedOut: false };
  }
}
