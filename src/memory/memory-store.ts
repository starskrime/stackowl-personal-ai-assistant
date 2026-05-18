import * as lancedb from "@lancedb/lancedb";
import { createRequire } from "node:module";
import { join } from "node:path";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { log } from "../logger.js";
import type { Fact } from "./fact-schema.js";

// ─── Types ────────────────────────────────────────────────────────

interface FactRow {
  fact_id: string;
  type: string;
  content: string;
  confidence: number;
  source: string;            // sessionId
  confirmation_count: number;
  contradictions: string;   // JSON array of factIds
  owl_name: string;
  user_id: string;
  created_at: string;
  vector: number[];          // 384-dim bge-small-en-v1.5
}

// ─── Kuzu types (CJS module via require) ─────────────────────────

const require = createRequire(import.meta.url);

interface KuzuDatabase { close(): void }
interface KuzuConnection { query(q: string, params?: Record<string, unknown>): Promise<KuzuResult> }
interface KuzuResult { getAll(): Promise<Record<string, unknown>[]> }
interface KuzuModule {
  Database: new (path: string) => KuzuDatabase;
  Connection: new (db: KuzuDatabase) => KuzuConnection;
}

// ─── MemoryStore ─────────────────────────────────────────────────

export class MemoryStore {
  private static readonly LANCE_TABLE = "memory_facts";
  private static readonly EMBED_DIM = 384; // bge-small-en-v1.5

  private lanceDb: lancedb.Connection | null = null;
  private lanceTable: lancedb.Table | null = null;
  private kuzuDb: KuzuDatabase | null = null;
  private kuzuConn: KuzuConnection | null = null;

  constructor(private workspacePath: string) {}

  async init(): Promise<void> {
    log.engine.info("[MemoryStore] init: entry", { workspacePath: this.workspacePath });
    await this._initLanceDB();
    await this._initKuzu();
    log.engine.info("[MemoryStore] init: exit — both stores ready");
  }

  // ─── Write (Kuzu first, then LanceDB) ──────────────────────────

  async upsert(fact: Fact, vector: number[]): Promise<void> {
    log.engine.debug("[MemoryStore] upsert: entry", { factId: fact.factId, type: fact.type });

    // 1. Kuzu first — safer rollback if LanceDB fails
    await this._kuzuUpsertFact(fact);

    // 2. LanceDB second
    const row: FactRow = {
      fact_id: fact.factId,
      type: fact.type,
      content: fact.content,
      confidence: fact.confidence,
      source: fact.source,
      confirmation_count: fact.confirmationCount,
      contradictions: JSON.stringify(fact.contradictions),
      owl_name: fact.owlName,
      user_id: fact.userId,
      created_at: fact.createdAt,
      vector,
    };

    this.assertReady();
    try {
      // delete existing row if present, then add
      await this.lanceTable!.delete(`fact_id = '${fact.factId}'`).catch(() => {/* not found — ok */});
      await this.lanceTable!.add([row as unknown as Record<string, unknown>]);
    } catch (err) {
      log.engine.error("[MemoryStore] upsert: LanceDB write failed", err as Error, { factId: fact.factId });
      throw err;
    }

    log.engine.debug("[MemoryStore] upsert: exit", { factId: fact.factId });
  }

  async delete(factId: string): Promise<void> {
    log.engine.debug("[MemoryStore] delete: entry", { factId });

    // Kuzu first
    await this._kuzuExec(
      `MATCH (f:Fact {fact_id: $factId}) DETACH DELETE f`,
      { factId },
    ).catch((err) => {
      log.engine.warn("[MemoryStore] delete: Kuzu delete failed (node may not exist)", { factId, err: String(err) });
    });

    // LanceDB second
    this.assertReady();
    await this.lanceTable!.delete(`fact_id = '${factId}'`).catch((err) => {
      log.engine.warn("[MemoryStore] delete: LanceDB delete failed", { factId, err: String(err) });
    });

    log.engine.debug("[MemoryStore] delete: exit", { factId });
  }

  // ─── Read ───────────────────────────────────────────────────────

  async search(queryVector: number[], topK: number): Promise<Fact[]> {
    log.engine.debug("[MemoryStore] search: entry", { topK });
    this.assertReady();

    try {
      const results = await this.lanceTable!
        .vectorSearch(queryVector)
        .limit(topK)
        .toArray();

      const facts = (results as FactRow[]).map((r) => this._rowToFact(r));
      log.engine.debug("[MemoryStore] search: exit", { found: facts.length });
      return facts;
    } catch (err) {
      log.engine.error("[MemoryStore] search: LanceDB search failed", err as Error, { topK });
      throw err;
    }
  }

  async getExisting(owlName: string, userId: string, limit = 200): Promise<Fact[]> {
    log.engine.debug("[MemoryStore] getExisting: entry", { owlName, userId });
    this.assertReady();

    try {
      const rows = await this.lanceTable!
        .query()
        .where(`owl_name = '${owlName}' AND user_id = '${userId}'`)
        .limit(limit)
        .toArray();

      const facts = (rows as FactRow[]).map((r) => this._rowToFact(r));
      log.engine.debug("[MemoryStore] getExisting: exit", { count: facts.length });
      return facts;
    } catch (err) {
      log.engine.error("[MemoryStore] getExisting: failed", err as Error, { owlName, userId });
      return [];
    }
  }

  /** DreamWorker: fetch mistake/approach_failed/dream_reflection facts for nightly reflection. */
  async getDreamCandidates(limit = 10): Promise<Fact[]> {
    log.engine.debug("[MemoryStore] getDreamCandidates: entry");
    this.assertReady();

    try {
      // sort by contradictions count DESC (JSON array length heuristic), then createdAt DESC
      const rows = await this.lanceTable!
        .query()
        .where(`type IN ('approach_failed', 'dream_reflection')`)
        .limit(limit * 5) // over-fetch, then sort in JS
        .toArray();

      const facts = (rows as FactRow[]).map((r) => this._rowToFact(r));

      // Sort: most contradictions first, then newest
      facts.sort((a, b) => {
        const contDiff = b.contradictions.length - a.contradictions.length;
        if (contDiff !== 0) return contDiff;
        return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      });

      const top = facts.slice(0, limit);
      log.engine.debug("[MemoryStore] getDreamCandidates: exit", { count: top.length });
      return top;
    } catch (err) {
      log.engine.error("[MemoryStore] getDreamCandidates: failed", err as Error);
      return [];
    }
  }

  // ─── Kuzu helpers ───────────────────────────────────────────────

  private async _kuzuUpsertFact(fact: Fact): Promise<void> {
    // Upsert Fact node
    try {
      await this._kuzuExec(
        `CREATE (f:Fact {fact_id: $factId, type: $type, owl_name: $owlName, user_id: $userId, confidence: $confidence, created_at: $createdAt})`,
        {
          factId: fact.factId,
          type: fact.type,
          owlName: fact.owlName,
          userId: fact.userId,
          confidence: fact.confidence,
          createdAt: fact.createdAt,
        },
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("duplicate") || msg.includes("already exists") || msg.includes("violates")) {
        await this._kuzuExec(
          `MATCH (f:Fact {fact_id: $factId}) SET f.confidence = $confidence`,
          { factId: fact.factId, confidence: fact.confidence },
        );
      } else {
        throw e;
      }
    }

    // Upsert Session node
    try {
      await this._kuzuExec(
        `CREATE (s:Session {session_id: $sessionId})`,
        { sessionId: fact.source },
      );
    } catch {/* session already exists — ok */}

    // SOURCED_FROM edge
    try {
      await this._kuzuExec(
        `MATCH (f:Fact {fact_id: $factId}), (s:Session {session_id: $sessionId}) CREATE (f)-[:SOURCED_FROM]->(s)`,
        { factId: fact.factId, sessionId: fact.source },
      );
    } catch {/* edge may already exist */}

    // CONTRADICTS edges
    for (const contradictedId of fact.contradictions) {
      try {
        await this._kuzuExec(
          `MATCH (a:Fact {fact_id: $a}), (b:Fact {fact_id: $b}) CREATE (a)-[:CONTRADICTS]->(b)`,
          { a: fact.factId, b: contradictedId },
        );
      } catch {/* ok */}
    }
  }

  private async _kuzuExec(query: string, params?: Record<string, unknown>): Promise<void> {
    if (!this.kuzuConn) throw new Error("[MemoryStore] Kuzu not initialized");
    await this.kuzuConn.query(query, params);
  }

  // ─── Init ───────────────────────────────────────────────────────

  private async _initLanceDB(): Promise<void> {
    const dbPath = join(this.workspacePath, ".memory_lance");
    if (!existsSync(dbPath)) await mkdir(dbPath, { recursive: true });

    this.lanceDb = await lancedb.connect(dbPath);
    const tables = await this.lanceDb.tableNames();

    if (tables.includes(MemoryStore.LANCE_TABLE)) {
      this.lanceTable = await this.lanceDb.openTable(MemoryStore.LANCE_TABLE);
      log.engine.info(`[MemoryStore] LanceDB: opened "${MemoryStore.LANCE_TABLE}"`);
    } else {
      const sentinel: FactRow = {
        fact_id: "__schema_sentinel__",
        type: "user_preference",
        content: "",
        confidence: 0,
        source: "",
        confirmation_count: 0,
        contradictions: "[]",
        owl_name: "",
        user_id: "",
        created_at: new Date().toISOString(),
        vector: new Array<number>(MemoryStore.EMBED_DIM).fill(0),
      };
      this.lanceTable = await this.lanceDb.createTable(MemoryStore.LANCE_TABLE, [sentinel as unknown as Record<string, unknown>]);
      await this.lanceTable.delete(`fact_id = '__schema_sentinel__'`);
      this.lanceTable = await this.lanceDb.openTable(MemoryStore.LANCE_TABLE);
      log.engine.info(`[MemoryStore] LanceDB: created "${MemoryStore.LANCE_TABLE}" (dim=${MemoryStore.EMBED_DIM})`);
    }
  }

  private async _initKuzu(): Promise<void> {
    const dbPath = join(this.workspacePath, ".memory_kuzu");
    if (!existsSync(dbPath)) await mkdir(dbPath, { recursive: true });

    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const kuzu = require("kuzu") as KuzuModule;
    this.kuzuDb = new kuzu.Database(dbPath);
    this.kuzuConn = new kuzu.Connection(this.kuzuDb);

    // Schema — IF NOT EXISTS guards are idempotent
    await this._kuzuExec(
      `CREATE NODE TABLE IF NOT EXISTS Fact(fact_id STRING, type STRING, owl_name STRING, user_id STRING, confidence DOUBLE, created_at STRING, PRIMARY KEY(fact_id))`,
    );
    await this._kuzuExec(
      `CREATE NODE TABLE IF NOT EXISTS Session(session_id STRING, PRIMARY KEY(session_id))`,
    );
    await this._kuzuExec(
      `CREATE REL TABLE IF NOT EXISTS CONTRADICTS(FROM Fact TO Fact)`,
    );
    await this._kuzuExec(
      `CREATE REL TABLE IF NOT EXISTS CONFIRMS(FROM Fact TO Fact)`,
    );
    await this._kuzuExec(
      `CREATE REL TABLE IF NOT EXISTS SOURCED_FROM(FROM Fact TO Session)`,
    );
    await this._kuzuExec(
      `CREATE REL TABLE IF NOT EXISTS LEARNED_FROM(FROM Fact TO Fact)`,
    );

    log.engine.info("[MemoryStore] Kuzu: schema ready");
  }

  // ─── Helpers ────────────────────────────────────────────────────

  private assertReady(): void {
    if (!this.lanceTable) throw new Error("[MemoryStore] LanceDB not initialized — call init() first");
  }

  private _rowToFact(r: FactRow): Fact {
    return {
      factId: r.fact_id,
      type: r.type as Fact["type"],
      content: r.content,
      confidence: r.confidence,
      source: r.source,
      confirmationCount: r.confirmation_count,
      contradictions: JSON.parse(r.contradictions ?? "[]") as string[],
      owlName: r.owl_name,
      userId: r.user_id,
      createdAt: r.created_at,
    };
  }
}
