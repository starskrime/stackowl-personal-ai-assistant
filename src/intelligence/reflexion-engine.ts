import { randomUUID } from "node:crypto";
import type { Database as BetterSqlite3 } from "better-sqlite3";
import type { ModelProvider } from "../providers/base.js";

// ─── Types ────────────────────────────────────────────────────────

/** Minimal shape accepted from MemoryDatabase or a raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

export interface TaskFailedArgs {
  userId: string;
  taskDescription: string;
  toolSequence: string[];
  errorSummary: string;
  category: string;
  complexityTier: string;
  /** Optional quality score (0–1). If below 0.3, critique is skipped. */
  qualityScore?: number;
}

// ─── ReflexionEngine ──────────────────────────────────────────────

/**
 * Writes 2-sentence self-critiques to `reflexion_critiques` after task failures.
 * These are later retrieved by `CritiqueRetriever` before similar tasks so the
 * owl can avoid repeating the same mistake — completing the Reflexion loop.
 *
 * Non-blocking: designed to run in the PostProcessor's background task queue.
 */
export class ReflexionEngine {
  private readonly raw: BetterSqlite3;

  constructor(
    db: DbWithRaw | BetterSqlite3,
    private readonly provider: ModelProvider,
    private readonly embedFn: (text: string) => Promise<number[]>,
  ) {
    // Accept either a MemoryDatabase wrapper (has .rawDb) or a raw db instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  /**
   * Called after a task failure. Generates a 2-sentence self-critique via LLM
   * and persists it to `reflexion_critiques` with an embedding for future retrieval.
   *
   * Skips silently when:
   *  - qualityScore is below 0.3 (low-quality signal — not worth learning from)
   *  - an identical (category, tool_sequence) entry already exists (deduplication)
   *  - the LLM call fails (non-blocking)
   */
  async onTaskFailed(args: TaskFailedArgs): Promise<void> {
    // Quality gate: very low-quality signals don't produce actionable critiques
    if ((args.qualityScore ?? 1) < 0.3) return;

    const toolKey = args.toolSequence.join(",");

    // Deduplication: skip if we already have a critique for this (category, tool_sequence)
    const existing = this.raw
      .prepare(
        "SELECT id FROM reflexion_critiques WHERE task_category = ? AND tool_sequence = ? LIMIT 1",
      )
      .get(args.category, toolKey);
    if (existing) return;

    const prompt = [
      `Task attempted: ${args.taskDescription}`,
      `Tools used in sequence: ${toolKey || "none"}`,
      `Final error encountered: ${args.errorSummary}`,
      `Write exactly 2 sentences: (1) why this failed, (2) what to try differently next time.`,
    ].join("\n");

    let critiqueText: string;
    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { maxTokens: 120, temperature: 0.3 },
      );
      critiqueText = response.content.trim();
    } catch {
      // Non-blocking — swallow errors so background queue keeps running
      return;
    }

    // Embed the critique for cosine-similarity retrieval
    const embedding = await this.embedFn(critiqueText);
    const buf = Buffer.allocUnsafe(embedding.length * 4);
    embedding.forEach((v, i) => buf.writeFloatLE(v, i * 4));

    this.raw
      .prepare(
        `INSERT INTO reflexion_critiques
           (id, task_category, complexity_tier, tool_sequence, critique_text, embedding, used_count, created_at)
         VALUES (?, ?, ?, ?, ?, ?, 0, ?)`,
      )
      .run(
        randomUUID(),
        args.category,
        args.complexityTier,
        toolKey,
        critiqueText,
        buf,
        new Date().toISOString(),
      );
  }
}
