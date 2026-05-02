import type { Database as BetterSqlite3 } from "better-sqlite3";

// ─── Types ────────────────────────────────────────────────────────

/** Minimal shape accepted from MemoryDatabase or a raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

// ─── OwlStateReporter ─────────────────────────────────────────────

/**
 * Produces a plain-text snapshot of an owl's current observable state:
 * memory (fact count + pellet count), active task, and most recent learning.
 *
 * Designed to be called from both CLI (/owl) and Telegram (/owl) handlers.
 */
export class OwlStateReporter {
  private readonly raw: BetterSqlite3;

  constructor(db: DbWithRaw | BetterSqlite3) {
    // Accept either a MemoryDatabase wrapper (has .rawDb) or a raw db instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  async report(
    userId: string,
    owlName: string,
    dna?: Record<string, unknown>,
  ): Promise<string> {
    const factCount = (
      this.raw
        .prepare(
          "SELECT COUNT(*) as n FROM facts WHERE user_id = ? AND invalidated_at IS NULL",
        )
        .get(userId) as { n: number }
    ).n;

    const pelletCount = (() => {
      try {
        return (
          this.raw
            .prepare("SELECT COUNT(*) as n FROM pellets WHERE user_id = ?")
            .get(userId) as { n: number }
        ).n;
      } catch {
        return 0;
      }
    })();

    const lastFact = this.raw
      .prepare(
        "SELECT updated_at FROM facts WHERE user_id = ? AND invalidated_at IS NULL ORDER BY updated_at DESC LIMIT 1",
      )
      .get(userId) as { updated_at: string } | undefined;

    const activeTask = this.raw
      .prepare(
        "SELECT subgoal_text, subgoal_index, created_at FROM owl_task_ledger WHERE user_id = ? AND status = 'in_progress' ORDER BY created_at DESC LIMIT 1",
      )
      .get(userId) as
      | { subgoal_text: string; subgoal_index: number; created_at: string }
      | undefined;

    const recentLearning = this.raw
      .prepare(
        "SELECT fact FROM facts WHERE user_id = ? AND source = 'owl_inferred' AND invalidated_at IS NULL ORDER BY created_at DESC LIMIT 1",
      )
      .get(userId) as { fact: string } | undefined;

    const lines: string[] = [];

    let header = `Owl: ${owlName}`;
    if (dna) {
      const dnaFields = ["challengeLevel", "verbosity"]
        .map((k) => `${k}=${dna[k] ?? "?"}`)
        .join(" · ");
      header += `  |  DNA: ${dnaFields}`;
    }
    lines.push(header);

    const ago = lastFact ? timeSince(lastFact.updated_at) : "never";
    lines.push(
      `Memory: ${factCount} fact${factCount !== 1 ? "s" : ""} · ${pelletCount} pellet${pelletCount !== 1 ? "s" : ""} · last updated ${ago}`,
    );

    if (activeTask) {
      lines.push(
        `Active task: step ${activeTask.subgoal_index + 1} — "${activeTask.subgoal_text}"`,
      );
    }

    if (recentLearning) {
      lines.push(`Recent learning: "${recentLearning.fact}"`);
    }

    return lines.join("\n");
  }
}

// ─── Helpers ─────────────────────────────────────────────────────

function timeSince(isoDate: string): string {
  const ms = Date.now() - new Date(isoDate).getTime();
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
