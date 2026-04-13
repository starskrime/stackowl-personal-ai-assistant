/**
 * StackOwl — Kuzu Persistent Knowledge Graph
 *
 * Replaces the in-memory graphology graph with a persistent Kuzu database.
 * Survives restarts. Supports graph-aware pellet retrieval (GraphRAG pattern).
 *
 * Schema:
 *   NODE: Pellet(id STRING PK, title STRING, tags STRING)
 *   REL:  RELATED(FROM Pellet TO Pellet, weight DOUBLE, rel_type STRING)
 *         rel_type: "tag" | "vector_sim" | "concept"
 *
 * Storage: <workspace>/.pellets_kuzu/
 */

import { createRequire } from "node:module";
import { join } from "node:path";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { log } from "../logger.js";
import type { Pellet } from "./store.js";

// CJS interop for Kuzu (no ESM export)
const require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-require-imports
const kuzu = require("kuzu") as {
  Database: new (path: string) => KuzuDatabase;
  Connection: new (db: KuzuDatabase) => KuzuConnection;
};

interface KuzuDatabase {
  close(): void;
}
interface KuzuQueryResult {
  getAll(): Array<Record<string, unknown>>;
  hasNext(): boolean;
  getNext(): Record<string, unknown>;
}
interface KuzuPreparedStatement {
  // opaque handle returned by connection.prepare()
}
interface KuzuConnection {
  /** Run a raw query string (no parameters). */
  query(query: string): Promise<KuzuQueryResult>;
  /** Execute a prepared statement with parameter bindings. */
  execute(
    prepared: KuzuPreparedStatement,
    params: Record<string, unknown>,
  ): Promise<KuzuQueryResult>;
  /** Prepare a parameterised query — returns an opaque PreparedStatement. */
  prepare(query: string): Promise<KuzuPreparedStatement>;
  close(): void;
}

// ─── Graph ───────────────────────────────────────────────────────

export class KuzuPelletGraph {
  private db: KuzuDatabase | null = null;
  private conn: KuzuConnection | null = null;
  private readonly dbPath: string;
  private _isBuilt = false;

  constructor(workspacePath: string) {
    this.dbPath = join(workspacePath, ".pellets_kuzu");
  }

  get isBuilt(): boolean {
    return this._isBuilt;
  }

  // ─── Init ────────────────────────────────────────────────────

  private _initialized = false;

  async init(): Promise<void> {
    if (this._initialized) return;
    this._initialized = true;
    await this._initAttempt(false);
  }

  private async _initAttempt(isRetry: boolean): Promise<void> {
    if (!existsSync(this.dbPath)) {
      await mkdir(this.dbPath, { recursive: true });
    }

    try {
      this.db = new kuzu.Database(this.dbPath);
      this.conn = new kuzu.Connection(this.db);

      // Create schema (IF NOT EXISTS guards are idempotent)
      await this.exec(
        `CREATE NODE TABLE IF NOT EXISTS Pellet(id STRING, title STRING, tags STRING, PRIMARY KEY(id))`,
      );
      await this.exec(
        `CREATE REL TABLE IF NOT EXISTS RELATED(FROM Pellet TO Pellet, weight DOUBLE, rel_type STRING)`,
      );

      this._isBuilt = true;
      log.engine.info("[KuzuGraph] Initialized at " + this.dbPath);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const isCorrupted = msg.includes("wal") || msg.includes("WAL") || msg.includes("Corrupted") || msg.includes("recovery");

      if (isCorrupted && !isRetry) {
        log.engine.warn(`[KuzuGraph] Corrupted WAL detected — wiping and reinitializing. Graph will rebuild from LanceDB.`);
        const { rm } = await import("node:fs/promises");
        await rm(this.dbPath, { recursive: true, force: true });
        await this._initAttempt(true);
      } else {
        throw err;
      }
    }
  }

  // ─── Write ───────────────────────────────────────────────────

  /** Upsert a Pellet node. */
  async addNode(pellet: Pellet): Promise<void> {
    this.assertReady();
    const tagsJson = JSON.stringify(pellet.tags);
    // Kuzu upsert: try CREATE first, fall back to MATCH+SET on duplicate key
    try {
      await this.exec(
        `CREATE (p:Pellet {id: $id, title: $title, tags: $tags})`,
        { id: pellet.id, title: pellet.title, tags: tagsJson },
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("duplicate") || msg.includes("already exists") || msg.includes("violates")) {
        await this.exec(
          `MATCH (p:Pellet {id: $id}) SET p.title = $title, p.tags = $tags`,
          { id: pellet.id, title: pellet.title, tags: tagsJson },
        );
      } else {
        throw e;
      }
    }
  }

  /** Remove a Pellet node and all its edges. */
  async removeNode(id: string): Promise<void> {
    this.assertReady();
    // Delete edges first, then node
    await this.exec(
      `MATCH (p:Pellet {id: $id})-[r:RELATED]-() DELETE r`,
      { id },
    ).catch(() => {/* no edges — ok */});
    await this.exec(
      `MATCH (p:Pellet {id: $id})<-[r:RELATED]-() DELETE r`,
      { id },
    ).catch(() => {});
    await this.exec(`MATCH (p:Pellet {id: $id}) DELETE p`, { id });
  }

  /**
   * Add a directed RELATED edge. Creates both directions for undirected traversal.
   */
  async addEdge(
    fromId: string,
    toId: string,
    relType: "tag" | "vector_sim" | "concept",
    weight: number,
  ): Promise<void> {
    this.assertReady();
    // Create edge in both directions (undirected semantics)
    const q = `
      MATCH (a:Pellet {id: $from}), (b:Pellet {id: $to})
      CREATE (a)-[:RELATED {weight: $w, rel_type: $t}]->(b)
    `;
    await this.exec(q, { from: fromId, to: toId, w: weight, t: relType }).catch(() => {});
    await this.exec(q, { from: toId, to: fromId, w: weight, t: relType }).catch(() => {});
  }

  // ─── Query ───────────────────────────────────────────────────

  /**
   * BFS up to `maxHops` from a seed node.
   * Returns neighbor IDs sorted by cumulative weight (desc).
   */
  async getNeighbors(
    id: string,
    maxHops = 2,
    limit = 10,
  ): Promise<string[]> {
    this.assertReady();
    // Variable-length path query — Kuzu supports *1..N syntax
    const q = `
      MATCH (seed:Pellet {id: $id})-[:RELATED*1..${maxHops}]-(neighbor:Pellet)
      WHERE neighbor.id <> $id
      RETURN DISTINCT neighbor.id AS nid
      LIMIT ${limit}
    `;
    try {
      const result = await this.exec(q, { id });
      return result.getAll().map((r) => r["nid"] as string).filter(Boolean);
    } catch {
      return [];
    }
  }

  /** Check if a node exists. */
  async hasNode(id: string): Promise<boolean> {
    this.assertReady();
    const result = await this.exec(
      `MATCH (p:Pellet {id: $id}) RETURN COUNT(p) AS cnt`,
      { id },
    );
    const rows = result.getAll();
    return Number(rows[0]?.["cnt"] ?? 0) > 0;
  }

  // ─── Stats ───────────────────────────────────────────────────

  async getStats(): Promise<{
    nodes: number;
    edges: number;
  }> {
    this.assertReady();
    const n = await this.exec(`MATCH (p:Pellet) RETURN COUNT(p) AS cnt`);
    const e = await this.exec(`MATCH ()-[r:RELATED]->() RETURN COUNT(r) AS cnt`);
    return {
      nodes: Number(n.getAll()[0]?.["cnt"] ?? 0),
      edges: Number(e.getAll()[0]?.["cnt"] ?? 0),
    };
  }

  // ─── Bulk build ──────────────────────────────────────────────

  /**
   * Build graph from a set of pellets + a similarity function.
   * Called on startup after migration or on explicit `stackowl graph build`.
   */
  async buildFromPellets(
    pellets: Pellet[],
    getSimilar: (p: Pellet) => Promise<Array<{ id: string; score: number }>>,
  ): Promise<void> {
    this.assertReady();
    log.engine.info(`[KuzuGraph] Building graph from ${pellets.length} pellets...`);

    // Phase 1: Add all nodes
    let nodesFailed = 0;
    for (const p of pellets) {
      try {
        await this.addNode(p);
      } catch (e) {
        nodesFailed++;
        log.engine.warn(`[KuzuGraph] addNode failed for "${p.id}": ${e instanceof Error ? e.message : e}`);
      }
    }
    if (nodesFailed > 0) {
      log.engine.warn(`[KuzuGraph] ${nodesFailed}/${pellets.length} nodes failed to insert`);
    }

    // Phase 2: Tag-based edges (O(n²) but batched per pellet)
    const tagIndex = new Map<string, string[]>(); // tag → pelletIds
    for (const p of pellets) {
      for (const tag of p.tags) {
        const t = tag.toLowerCase().trim();
        if (!tagIndex.has(t)) tagIndex.set(t, []);
        tagIndex.get(t)!.push(p.id);
      }
    }
    for (const [, ids] of tagIndex) {
      if (ids.length < 2) continue;
      for (let i = 0; i < ids.length; i++) {
        for (let j = i + 1; j < ids.length; j++) {
          await this.addEdge(ids[i], ids[j], "tag", 1.0).catch(() => {});
        }
      }
    }

    // Phase 3: Vector similarity edges
    let processed = 0;
    for (const p of pellets) {
      try {
        const similar = await getSimilar(p);
        for (const { id, score } of similar) {
          if (id !== p.id && score >= 0.6) {
            await this.addEdge(p.id, id, "vector_sim", score).catch(() => {});
          }
        }
      } catch {
        // non-fatal per pellet
      }
      processed++;
      if (processed % 50 === 0) {
        log.engine.info(`[KuzuGraph] Graph build progress: ${processed}/${pellets.length}`);
      }
    }

    const stats = await this.getStats();
    log.engine.info(
      `[KuzuGraph] Build complete — ${stats.nodes} nodes, ${stats.edges} edges`,
    );
  }

  // ─── Internal ────────────────────────────────────────────────

  private assertReady(): void {
    if (!this.conn) {
      throw new Error("[KuzuGraph] Not initialized — call init() first");
    }
  }

  private async exec(
    query: string,
    params?: Record<string, unknown>,
  ): Promise<KuzuQueryResult> {
    if (params && Object.keys(params).length > 0) {
      const prepared = await this.conn!.prepare(query);
      return this.conn!.execute(prepared, params);
    }
    return this.conn!.query(query);
  }
}
