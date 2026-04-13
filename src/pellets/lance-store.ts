/**
 * StackOwl — LanceDB Pellet Store
 *
 * Persistent vector store for pellets.
 * Every pellet is stored as a row with full metadata + embedding vector.
 * Cosine similarity search replaces BM25/TF-IDF.
 *
 * Storage: <workspace>/.pellets_lance/ (LanceDB columnar format)
 */

import * as lancedb from "@lancedb/lancedb";
import { join } from "node:path";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { log } from "../logger.js";
import type { Pellet } from "./store.js";
import { embed, pelletToEmbedText, getEmbeddingDim } from "./embedder.js";

// ─── Row schema ──────────────────────────────────────────────────

/** Flat LanceDB row (arrays serialized as JSON strings). */
export interface PelletRow {
  id: string;
  title: string;
  generated_at: string;
  source: string;
  owls: string; // JSON array
  tags: string; // JSON array
  content: string;
  version: number;
  supersedes: string; // empty string = null
  merged_from: string; // JSON array
  last_merged_at: string; // empty string = null
  vector: number[];
  [key: string]: unknown;
}

export interface SearchHit {
  pellet: Pellet;
  /** Cosine similarity (0–1, higher = more similar) */
  score: number;
}

// ─── Serialization ───────────────────────────────────────────────

function pelletToRow(pellet: Pellet, vector: number[]): PelletRow {
  return {
    id: pellet.id,
    title: pellet.title,
    generated_at: pellet.generatedAt,
    source: pellet.source,
    owls: JSON.stringify(pellet.owls),
    tags: JSON.stringify(pellet.tags),
    content: pellet.content,
    version: pellet.version,
    supersedes: pellet.supersedes ?? "",
    merged_from: JSON.stringify(pellet.mergedFrom ?? []),
    last_merged_at: pellet.lastMergedAt ?? "",
    vector,
  };
}

function rowToPellet(row: Record<string, unknown>): Pellet {
  const p: Pellet = {
    id: row["id"] as string,
    title: row["title"] as string,
    generatedAt: (row["generated_at"] as string) || new Date().toISOString(),
    source: (row["source"] as string) || "unknown",
    owls: safeParseJson<string[]>(row["owls"] as string, []),
    tags: safeParseJson<string[]>(row["tags"] as string, []),
    content: (row["content"] as string) || "",
    version: (row["version"] as number) || 1,
  };
  const sup = row["supersedes"] as string;
  if (sup) p.supersedes = sup;
  const mf = safeParseJson<string[]>(row["merged_from"] as string, []);
  if (mf.length > 0) p.mergedFrom = mf;
  const lma = row["last_merged_at"] as string;
  if (lma) p.lastMergedAt = lma;
  return p;
}

function safeParseJson<T>(raw: string | null | undefined, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

// ─── LancePelletStore ────────────────────────────────────────────

export class LancePelletStore {
  private db: lancedb.Connection | null = null;
  private table: lancedb.Table | null = null;
  private readonly dbPath: string;
  private static readonly TABLE = "pellets";

  constructor(workspacePath: string) {
    this.dbPath = join(workspacePath, ".pellets_lance");
  }

  // ─── Init ──────────────────────────────────────────────────────

  private _initialized = false;

  async init(): Promise<void> {
    if (this._initialized) return;
    this._initialized = true;

    if (!existsSync(this.dbPath)) {
      await mkdir(this.dbPath, { recursive: true });
    }

    this.db = await lancedb.connect(this.dbPath);
    const tables = await this.db.tableNames();

    if (tables.includes(LancePelletStore.TABLE)) {
      this.table = await this.db.openTable(LancePelletStore.TABLE);
      log.engine.info(
        `[LanceStore] Opened table "${LancePelletStore.TABLE}" (${await this.count()} rows)`,
      );
    } else {
      // Create table with a sentinel row to establish schema, then delete it
      const dim = getEmbeddingDim();
      const sentinel: PelletRow = {
        id: "__schema_sentinel__",
        title: "",
        generated_at: new Date().toISOString(),
        source: "",
        owls: "[]",
        tags: "[]",
        content: "",
        version: 1,
        supersedes: "",
        merged_from: "[]",
        last_merged_at: "",
        vector: new Array<number>(dim).fill(0),
      };
      this.table = await this.db.createTable(LancePelletStore.TABLE, [sentinel]);
      await this.table.delete(`id = '${sentinel.id}'`);
      log.engine.info(
        `[LanceStore] Created table "${LancePelletStore.TABLE}" (dim=${dim})`,
      );
    }
  }

  // ─── Write ─────────────────────────────────────────────────────

  /**
   * Upsert a pellet (insert or update by id).
   * Embeds the pellet text if no vector is provided.
   */
  async upsert(pellet: Pellet, vector?: number[]): Promise<void> {
    this.assertReady();

    const vec = vector ?? (await embed(pelletToEmbedText(pellet))) ?? new Array<number>(getEmbeddingDim()).fill(0);
    const row = pelletToRow(pellet, vec);

    // LanceDB mergeInsert: update if id matches, insert otherwise
    await this.table!
      .mergeInsert("id")
      .whenMatchedUpdateAll()
      .whenNotMatchedInsertAll()
      .execute([row]);
  }

  /** Delete a pellet by id. */
  async delete(id: string): Promise<void> {
    this.assertReady();
    await this.table!.delete(`id = '${this.esc(id)}'`);
  }

  // ─── Read ──────────────────────────────────────────────────────

  /** Fetch a single pellet by id. Returns null if not found. */
  async get(id: string): Promise<Pellet | null> {
    this.assertReady();
    const rows = await this.table!
      .query()
      .where(`id = '${this.esc(id)}'`)
      .limit(1)
      .toArray();
    return rows.length > 0 ? rowToPellet(rows[0] as Record<string, unknown>) : null;
  }

  /** Fetch multiple pellets by id in one scan. */
  async getByIds(ids: string[]): Promise<Pellet[]> {
    if (ids.length === 0) return [];
    this.assertReady();
    const set = new Set(ids);
    const all = await this.listAll();
    return all.filter((p) => set.has(p.id));
  }

  /** Return all pellets, sorted by generatedAt desc. */
  async listAll(): Promise<Pellet[]> {
    this.assertReady();
    const rows = await this.table!.query().toArray();
    return (rows as Record<string, unknown>[])
      .map(rowToPellet)
      .sort(
        (a, b) =>
          new Date(b.generatedAt).getTime() - new Date(a.generatedAt).getTime(),
      );
  }

  /** Total number of pellets. */
  async count(): Promise<number> {
    this.assertReady();
    return this.table!.countRows();
  }

  // ─── Search ────────────────────────────────────────────────────

  /**
   * Cosine similarity search.
   * `maxDistance` is cosine distance (0 = identical, 2 = opposite).
   * Typical useful range: < 0.5 (cosine_sim > 0.5)
   */
  async searchSimilar(
    queryVec: number[],
    limit = 10,
    maxDistance = 0.5,
  ): Promise<SearchHit[]> {
    this.assertReady();

    const results = await this.table!
      .vectorSearch(queryVec)
      .distanceType("cosine")
      .limit(limit * 2) // over-fetch then filter by distance
      .toArray();

    return (results as Array<Record<string, unknown>>)
      .filter((r) => {
        const d = r["_distance"] as number;
        return typeof d === "number" && d <= maxDistance;
      })
      .slice(0, limit)
      .map((r) => ({
        pellet: rowToPellet(r),
        score: 1 - (r["_distance"] as number), // convert distance → similarity
      }));
  }

  /**
   * Search similar to a given pellet (for dedup).
   * Excludes the pellet's own id from results.
   */
  async findSimilarTo(
    pellet: Pellet,
    limit = 3,
    maxDistance = 0.45,
  ): Promise<SearchHit[]> {
    const vec = await embed(pelletToEmbedText(pellet));
    if (!vec) return [];

    const hits = await this.searchSimilar(vec, limit + 1, maxDistance);
    return hits.filter((h) => h.pellet.id !== pellet.id).slice(0, limit);
  }

  // ─── Migration ─────────────────────────────────────────────────

  /**
   * Bulk-import existing pellets into LanceDB.
   * Used on first startup to migrate from the old markdown-file store.
   */
  async migrate(pellets: Pellet[]): Promise<void> {
    if (pellets.length === 0) return;
    log.engine.info(`[LanceStore] Embedding and inserting ${pellets.length} pellets...`);

    let ok = 0;
    let fail = 0;
    const total = pellets.length;
    const REPORT_EVERY = 100;

    for (const pellet of pellets) {
      try {
        const vec = await embed(pelletToEmbedText(pellet));
        await this.upsert(pellet, vec ?? undefined);
        ok++;
      } catch (err) {
        fail++;
        log.engine.warn(
          `[LanceStore] Migration failed for "${pellet.id}": ${err instanceof Error ? err.message : err}`,
        );
      }
      if ((ok + fail) % REPORT_EVERY === 0) {
        log.engine.info(
          `[LanceStore] Migration progress: ${ok + fail}/${total} (${ok} ok, ${fail} failed)`,
        );
      }
    }
    log.engine.info(`[LanceStore] Migration complete — ${ok}/${total} ok, ${fail} failed`);
  }

  // ─── Internal ──────────────────────────────────────────────────

  private assertReady(): void {
    if (!this.table) throw new Error("[LanceStore] Not initialized — call init() first");
  }

  /** Escape single quotes in SQL WHERE clauses. */
  private esc(s: string): string {
    return s.replace(/'/g, "''");
  }
}
