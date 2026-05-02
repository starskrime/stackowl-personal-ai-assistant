import { randomUUID } from "node:crypto";
import type { Database as BetterSqlite3 } from "better-sqlite3";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../context/layer.js";

// ─── Helpers ──────────────────────────────────────────────────────

/** Minimal shape needed from MemoryDatabase or any raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

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

// ─── SkillTemplateLayer ───────────────────────────────────────────

/**
 * Stores successful tool sequences as NL templates in `skill_templates`
 * (schema v17) and injects the best matching one into the LLM context as
 * `<proven_approach>`. This teaches the owl to repeat successful patterns.
 */
export class SkillTemplateLayer {
  /** Optional embedding function; injected in production, overridable in tests. */
  embedFn?: (text: string) => Promise<number[]>;

  private readonly raw: BetterSqlite3;

  constructor(db: DbWithRaw | BetterSqlite3) {
    // Accept either a MemoryDatabase wrapper (has `.rawDb`) or a raw DB instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  /**
   * Persist a new NL skill template derived from a successful tool sequence.
   * If a template with the same name already exists, it is updated in-place.
   */
  async storeTemplate(
    name: string,
    templateText: string,
    triggerDesc: string,
    source: "auto" | "marketplace" | "user" = "auto",
  ): Promise<void> {
    if (!this.embedFn) return;

    const embedding = await this.embedFn(triggerDesc);
    const buf = Buffer.allocUnsafe(embedding.length * 4);
    embedding.forEach((v, i) => buf.writeFloatLE(v, i * 4));

    this.raw
      .prepare(
        `INSERT INTO skill_templates
           (id, name, source, template_text, trigger_desc, embedding, success_count, installed_at)
         VALUES (?, ?, ?, ?, ?, ?, 0, ?)
         ON CONFLICT(name) DO UPDATE SET
           template_text = excluded.template_text,
           trigger_desc  = excluded.trigger_desc`,
      )
      .run(
        randomUUID(),
        name,
        source,
        templateText,
        triggerDesc,
        buf,
        new Date().toISOString(),
      );
  }

  /**
   * Retrieve the best-matching template for `query`.
   *
   * @returns A `<proven_approach>…</proven_approach>` XML block, or `""` if
   *          nothing relevant is found or no embedding function is configured.
   */
  async retrieve(query: string): Promise<string> {
    const rows = this.raw
      .prepare(
        `SELECT template_text, trigger_desc, embedding
           FROM skill_templates
          ORDER BY success_count DESC
          LIMIT 30`,
      )
      .all() as { template_text: string; trigger_desc: string; embedding: Buffer }[];

    if (rows.length === 0 || !this.embedFn) return "";

    const queryVec = await this.embedFn(query);

    const scored = rows.map((row) => {
      const arr = new Float32Array(
        row.embedding.buffer,
        row.embedding.byteOffset,
        row.embedding.byteLength / 4,
      );
      return { text: row.template_text, score: cosineSim(queryVec, Array.from(arr)) };
    });

    scored.sort((a, b) => b.score - a.score);

    const top = scored.find((s) => s.score > 0.75);
    if (!top) return "";

    return `<proven_approach>\n${top.text}\n</proven_approach>`;
  }

  /**
   * Returns a `ContextLayer` object that the `ContextPipeline` can consume.
   * Skips conversational messages — only fires for task-oriented requests.
   */
  asContextLayer(): ContextLayer {
    return {
      name:      "skill-template",
      priority:  8,
      maxTokens: 150,
      produces:  ["proven_approach"],
      dependsOn: [],

      shouldFire(triage: TriageSignals): boolean {
        return !triage.isConversational;
      },

      build: async (
        req: ContextRequest,
        _triage: TriageSignals,
        _deps: LayerResults,
      ): Promise<string> => {
        const msg: string =
          (req.session?.messages as Array<{ role: string; content: string }> | undefined)
            ?.filter((m) => m.role === "user")
            .at(-1)
            ?.content ??
          (req.messages as Array<{ role: string; content: string }> | undefined)
            ?.at(-1)
            ?.content ??
          "";
        return this.retrieve(msg);
      },
    };
  }
}
