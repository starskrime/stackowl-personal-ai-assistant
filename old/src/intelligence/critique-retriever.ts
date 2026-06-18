import type { Database as BetterSqlite3 } from "better-sqlite3";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../context/layer.js";

// ─── Types ────────────────────────────────────────────────────────

/** Minimal shape needed from MemoryDatabase or any raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

// ─── Helpers ──────────────────────────────────────────────────────

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += (a[i] ?? 0) * (b[i] ?? 0);
    na  += (a[i] ?? 0) ** 2;
    nb  += (b[i] ?? 0) ** 2;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d === 0 ? 0 : dot / d;
}

// ─── CritiqueRetriever ────────────────────────────────────────────

/**
 * Reads past failure lessons from `reflexion_critiques` (schema v17) and
 * injects them into the LLM context as a `<past_lessons>` block before the
 * owl starts a task. Implements the Reflexion pattern:
 * "retrieve past critiques before similar tasks".
 */
export class CritiqueRetriever {
  /** Optional embedding function; injected in production, overridable in tests. */
  embedFn?: (text: string) => Promise<number[]>;

  private readonly raw: BetterSqlite3;

  constructor(db: DbWithRaw | BetterSqlite3) {
    // Accept either a MemoryDatabase wrapper (has `.rawDb`) or a raw DB instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  /**
   * Retrieve and format past critique lessons relevant to `query`.
   *
   * @returns A `<past_lessons>…</past_lessons>` XML block, or `""` if nothing
   *          relevant is found or no embedding function is configured.
   */
  async retrieve(
    query: string,
    _category: string,
    _tier: string,
  ): Promise<string> {
    const rows = this.raw
      .prepare(
        `SELECT critique_text, embedding
           FROM reflexion_critiques
          WHERE used_count < 20
          ORDER BY created_at DESC
          LIMIT 20`,
      )
      .all() as { critique_text: string; embedding: Buffer }[];

    if (rows.length === 0 || !this.embedFn) return "";

    const queryVec = await this.embedFn(query);

    const scored = rows.map((row) => {
      const arr = new Float32Array(
        row.embedding.buffer,
        row.embedding.byteOffset,
        row.embedding.byteLength / 4,
      );
      const rowVec = Array.from(arr);
      return { text: row.critique_text, score: cosineSim(queryVec, rowVec) };
    });

    scored.sort((a, b) => b.score - a.score);

    const top = scored.filter((s) => s.score > 0.70).slice(0, 2);
    if (top.length === 0) return "";

    const lessons = top.map((s) => s.text).join("\n");
    return `<past_lessons>\n${lessons}\n</past_lessons>`;
  }

  /**
   * Returns a `ContextLayer` object that the `ContextPipeline` can consume.
   * Skips conversational messages — only fires for task-oriented requests.
   */
  asContextLayer(): ContextLayer {
    return {
      name:       "critique-retriever",
      priority:   9,
      maxTokens:  200,
      produces:   ["past_lessons"],
      dependsOn:  [],

      shouldFire(triage: TriageSignals): boolean {
        return !triage.isConversational;
      },

      build: async (
        req: ContextRequest,
        _triage: TriageSignals,
        _deps: LayerResults,
      ): Promise<string> => {
        // Best-effort: pull the last user message from whatever shape req provides
        const msg: string =
          (req.session?.messages as Array<{ role: string; content: string }> | undefined)
            ?.filter((m) => m.role === "user")
            .at(-1)
            ?.content ?? "";
        return this.retrieve(msg, "general", "medium");
      },
    };
  }
}
