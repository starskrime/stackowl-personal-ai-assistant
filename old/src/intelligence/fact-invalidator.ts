import type { Database as BetterSqlite3 } from "better-sqlite3";

// ─── Constants ────────────────────────────────────────────────────

const TEMPORAL_TRIGGERS = [
  "moved to", "now at", "now works at", "switched to",
  "no longer", "changed to", "actually", "left ", "quit ",
  "joined ", "starting at", "used to ", "recently moved",
];

const SIMILARITY_THRESHOLD = 0.85;
const ENTITY_OVERLAP_THRESHOLD = 0.1;
const CANDIDATE_LIMIT = 30;

// ─── Helpers ──────────────────────────────────────────────────────

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += (a[i] ?? 0) * (b[i] ?? 0);
    na += (a[i] ?? 0) ** 2;
    nb += (b[i] ?? 0) ** 2;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d === 0 ? 0 : dot / d;
}

/**
 * Entity overlap: Jaccard similarity over bag-of-words (no length filter so that
 * short pronouns like "user" can act as entity anchors).
 */
function entityOverlap(a: string, b: string): number {
  const aWords = new Set(a.toLowerCase().split(/\s+/).filter(w => w.length > 0));
  const bWords = new Set(b.toLowerCase().split(/\s+/).filter(w => w.length > 0));
  if (aWords.size === 0 || bWords.size === 0) return 0;
  const intersection = [...aWords].filter(w => bWords.has(w)).length;
  const union = new Set([...aWords, ...bWords]).size;
  return intersection / union;
}

// ─── DbWithRaw ────────────────────────────────────────────────────

/** Minimal shape accepted from MemoryDatabase or a raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

// ─── FactInvalidator ──────────────────────────────────────────────

/**
 * Heuristic temporal fact invalidation — no LLM required.
 *
 * When a new fact contains a temporal trigger phrase (e.g. "moved to",
 * "no longer", "switched to") AND is semantically similar to an existing
 * active fact AND shares entity overlap with it, the old fact is marked
 * `invalidated_at` so it no longer surfaces in retrieval.
 *
 * Designed to be called from the `fact:extracted` event handler.
 * `embedFn` is injected at wire-up time (Task 15).
 */
export class FactInvalidator {
  /**
   * Embedding function — injected at runtime (Task 15 wires in the provider).
   * When absent, `check()` is a no-op (safe to call before wiring).
   */
  embedFn?: (text: string) => Promise<number[]>;

  private readonly raw: BetterSqlite3;

  constructor(db: DbWithRaw | BetterSqlite3) {
    // Accept either a MemoryDatabase wrapper (has .rawDb) or a raw db instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  /**
   * Check whether `newFactText` temporally supersedes any existing fact for `userId`.
   * If so, stamps `invalidated_at` on the old fact row.
   *
   * Safe to call when `embedFn` is not yet set — exits early without touching the DB.
   */
  async check(newFactText: string, userId: string): Promise<void> {
    const hasTrigger = TEMPORAL_TRIGGERS.some(t =>
      newFactText.toLowerCase().includes(t),
    );
    if (!hasTrigger || !this.embedFn) return;

    const newVec = await this.embedFn(newFactText);

    const candidates = this.raw.prepare(`
      SELECT id, fact, embedding
      FROM facts
      WHERE user_id = ?
        AND invalidated_at IS NULL
        AND embedding IS NOT NULL
      ORDER BY created_at DESC
      LIMIT ${CANDIDATE_LIMIT}
    `).all(userId) as { id: string; fact: string; embedding: string }[];

    for (const candidate of candidates) {
      let parsed: number[];
      try {
        parsed = JSON.parse(candidate.embedding) as number[];
      } catch {
        continue; // malformed — skip
      }

      const sim = cosineSim(newVec, parsed);
      const overlap = entityOverlap(candidate.fact, newFactText);

      if (sim > SIMILARITY_THRESHOLD && overlap > ENTITY_OVERLAP_THRESHOLD) {
        this.raw.prepare(
          "UPDATE facts SET invalidated_at = ? WHERE id = ?",
        ).run(new Date().toISOString(), candidate.id);
      }
    }
  }
}
