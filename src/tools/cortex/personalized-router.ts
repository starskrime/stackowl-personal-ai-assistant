/**
 * StackOwl — Element 7 T11 — PersonalizedRouter
 *
 * Personalized Tool Routing (PTR): given the current user message, find the
 * top-K most similar past *successful* trajectories (cosine over embedded
 * `user_message`) and surface the tools that worked there. The result feeds
 * a `tool_prior` context layer (T12) that nudges the planner toward proven
 * tool sequences for this user — without forcing them.
 *
 * Cold-start: if fewer than COLD_START_THRESHOLD successful trajectories
 * exist in the window, return [] and let the planner pick freely.
 *
 * Embedding source: caller injects `embedFn` — production wires this to the
 * pellets embedder (`embed()` in `src/pellets/embedder.ts`); tests pass a
 * deterministic stub. We cache per-trajectory embeddings in-process to
 * avoid re-embedding the same `user_message` on every call.
 */
import type { MemoryDatabase } from "../../memory/db.js";
import { embed as defaultEmbed } from "../../pellets/embedder.js";
import { log } from "../../logger.js";

export type EmbedFn = (text: string) => Promise<number[] | null>;

export interface PersonalizedRouterOptions {
  topK?: number;
  windowDays?: number;
}

const COLD_START_THRESHOLD = 50;

export class PersonalizedRouter {
  private readonly cache = new Map<string, number[]>();

  constructor(
    private readonly db: MemoryDatabase,
    private readonly embedFn: EmbedFn = defaultEmbed,
  ) {}

  async suggestTools(
    userMessage: string,
    opts: PersonalizedRouterOptions = {},
  ): Promise<string[]> {
    const topK = opts.topK ?? 3;
    const windowDays = opts.windowDays ?? 30;

    log.tool.debug("personalized-router.suggestTools: entry", {
      messageLen: userMessage.length,
      topK,
      windowDays,
    });

    const trajectories = this.db.rawDb
      .prepare(
        `SELECT id, user_message FROM trajectories
            WHERE outcome = 'success'
              AND created_at > datetime('now', '-' || ? || ' days')`,
      )
      .all(windowDays) as Array<{ id: string; user_message: string }>;

    if (trajectories.length < COLD_START_THRESHOLD) {
      log.tool.debug("personalized-router.suggestTools: cold-start — insufficient history", {
        reason: "below COLD_START_THRESHOLD",
        trajectoryCount: trajectories.length,
        threshold: COLD_START_THRESHOLD,
      });
      return [];
    }

    log.tool.debug("personalized-router.suggestTools: trajectories loaded", {
      trajectoryCount: trajectories.length,
      windowDays,
    });

    const queryEmb = await this.embedFn(userMessage);
    if (!queryEmb) {
      log.tool.debug("personalized-router.suggestTools: embedding failed — returning empty", {
        reason: "embedFn returned null",
      });
      return [];
    }

    const scored: Array<{ id: string; score: number }> = [];
    for (const t of trajectories) {
      let emb = this.cache.get(t.id);
      if (!emb) {
        const e = await this.embedFn(t.user_message);
        if (!e) continue;
        emb = e;
        this.cache.set(t.id, emb);
      }
      scored.push({ id: t.id, score: cosine(queryEmb, emb) });
    }
    if (scored.length === 0) {
      log.tool.debug("personalized-router.suggestTools: no scoreable trajectories", {
        reason: "all embeddings null",
      });
      return [];
    }
    scored.sort((a, b) => b.score - a.score);

    const top = scored.slice(0, topK);
    log.tool.debug("personalized-router.suggestTools: top trajectories selected", {
      topK,
      topIds: top.map((t) => t.id),
      topScores: top.map((t) => t.score),
    });

    const placeholders = top.map(() => "?").join(",");
    const turns = this.db.rawDb
      .prepare(
        `SELECT tool_name FROM trajectory_turns
            WHERE trajectory_id IN (${placeholders})
            ORDER BY turn_index ASC`,
      )
      .all(...top.map((t) => t.id)) as Array<{ tool_name: string }>;

    const tools = new Set<string>();
    for (const turn of turns) tools.add(turn.tool_name);
    const result = [...tools];

    log.tool.debug("personalized-router.suggestTools: exit", {
      suggestedTools: result,
      success: true,
      resultLen: result.length,
    });

    return result;
  }
}

function cosine(a: number[], b: number[]): number {
  const len = Math.min(a.length, b.length);
  let dot = 0;
  let magA = 0;
  let magB = 0;
  for (let i = 0; i < len; i++) {
    dot += a[i]! * b[i]!;
    magA += a[i]! ** 2;
    magB += b[i]! ** 2;
  }
  return magA === 0 || magB === 0 ? 0 : dot / (Math.sqrt(magA) * Math.sqrt(magB));
}
