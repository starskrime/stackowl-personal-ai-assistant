import { v4 as uuidv4 } from "uuid";
import type { OutcomeJournal } from "./outcome-journal.js";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

interface QuietHour { start: number; end: number; }
interface SchedulerConfig { quietHours: QuietHour[]; }

export class ImprovementScheduler {
  private running = false;
  private timers: ReturnType<typeof setInterval>[] = [];

  constructor(
    private readonly journal: OutcomeJournal,
    private readonly db: MemoryDatabase,
    private readonly config: SchedulerConfig,
  ) {}

  start(): void {
    if (this.running) return;
    this.running = true;

    // Job 1: Journal review every 15 minutes (0 LLM calls)
    this.timers.push(setInterval(async () => {
      if (this.isInQuietHours()) return;
      try { await this.runJournalReview(); } catch (e) {
        log.engine.warn(`[ImprovementScheduler] Journal review error: ${e}`);
      }
    }, 15 * 60_000));

    // Job 2: Approach pruning every hour (0 LLM calls)
    this.timers.push(setInterval(async () => {
      if (this.isInQuietHours()) return;
      try { await this.runApproachPruning(); } catch (e) {
        log.engine.warn(`[ImprovementScheduler] Pruning error: ${e}`);
      }
    }, 60 * 60_000));

    log.engine.info("[ImprovementScheduler] Started — journal review (15min), pruning (1h)");
  }

  stop(): void {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    this.running = false;
  }

  isRunning(): boolean { return this.running; }

  isInQuietHours(): boolean {
    const hour = new Date().getHours();
    return this.config.quietHours.some(qh =>
      qh.start <= qh.end
        ? hour >= qh.start && hour < qh.end
        : hour >= qh.start || hour < qh.end
    );
  }

  /**
   * Aggregates recent failures into approach_patterns. Zero LLM calls.
   */
  async runJournalReview(): Promise<number> {
    const failures = await this.journal.getFailures({ minEntries: 5 });
    if (failures.length === 0) return 0;

    const byCategory = new Map<string, typeof failures>();
    for (const f of failures) {
      const cat = f.taskCategory ?? "general";
      if (!byCategory.has(cat)) byCategory.set(cat, []);
      byCategory.get(cat)!.push(f);
    }

    let processed = 0;
    for (const [category, entries] of byCategory) {
      if (entries.length < 3) continue;
      const lesson = `${entries.length} failures in "${category}" — check approach patterns`;
      const existing = this.db.rawDb.prepare(
        "SELECT id FROM approach_patterns WHERE task_category = ? AND lesson = ?"
      ).get(category, lesson);
      if (!existing) {
        this.db.rawDb.prepare(`
          INSERT INTO approach_patterns
            (id, task_category, lesson, observation_count, success_rate, status, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?)
        `).run(
          uuidv4(), category, lesson, entries.length, 0.0, "tentative",
          new Date().toISOString(), new Date().toISOString(),
        );
        processed++;
      }
    }
    return processed;
  }

  /**
   * Archives stale patterns, promotes proven ones. Zero LLM calls.
   */
  async runApproachPruning(): Promise<void> {
    const cutoff = new Date(Date.now() - 30 * 24 * 60 * 60_000).toISOString();
    this.db.rawDb.prepare(`
      UPDATE approach_patterns SET status = 'archived'
      WHERE status = 'tentative' AND (last_used_at IS NULL OR last_used_at < ?)
    `).run(cutoff);

    this.db.rawDb.prepare(`
      UPDATE approach_patterns SET status = 'proven'
      WHERE status = 'tentative' AND success_rate > 0.7 AND observation_count > 5
    `).run();
  }
}
