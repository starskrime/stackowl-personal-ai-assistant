import { v4 as uuidv4 } from "uuid";
import type { OutcomeJournal } from "./outcome-journal.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { SelfEvolver } from "../tools/cortex/self-evolver.js";
import type { ShadowRunner } from "../tools/cortex/shadow-runner.js";
import { log } from "../logger.js";

interface QuietHour { start: number; end: number; }
interface SchedulerConfig { quietHours: QuietHour[]; }

/**
 * Optional SET (Self-Evolving Tools) wiring. When both are present, the
 * scheduler runs `selfEvolver.runOnce(shadowRunner)` on a weekly cadence —
 * the hard safety constraint of "at most 1 SET rewrite per week" is enforced
 * here (cadence) and inside SelfEvolver itself (concurrency lock on
 * `tool_evolution_runs`).
 */
export interface ToolEvolutionDeps {
  selfEvolver: SelfEvolver;
  shadowRunner: ShadowRunner;
}

const WEEK_MS = 7 * 24 * 60 * 60_000;

export class ImprovementScheduler {
  private running = false;
  private timers: ReturnType<typeof setInterval>[] = [];

  constructor(
    private readonly journal: OutcomeJournal,
    private readonly db: MemoryDatabase,
    private readonly config: SchedulerConfig,
    private readonly toolEvolution?: ToolEvolutionDeps,
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

    // Job 3: SET tool evolution (weekly, only when wired)
    if (this.toolEvolution) {
      this.timers.push(setInterval(async () => {
        if (this.isInQuietHours()) return;
        try { await this.runToolEvolution(); } catch (e) {
          log.engine.warn(`[ImprovementScheduler] Tool evolution error: ${e}`);
        }
      }, WEEK_MS));
    }

    const jobs = this.toolEvolution
      ? "journal review (15min), pruning (1h), tool evolution (weekly)"
      : "journal review (15min), pruning (1h)";
    log.engine.info(`[ImprovementScheduler] Started — ${jobs}`);
  }

  /**
   * Run one SET cycle. Safe to call manually. Returns the run metadata when
   * a rewrite was started, or null when the cycle aborted at any gate.
   */
  async runToolEvolution() {
    if (!this.toolEvolution) return null;
    const { selfEvolver, shadowRunner } = this.toolEvolution;
    return selfEvolver.runOnce(shadowRunner);
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
      const lesson = `repeated failures in "${category}" — check approach patterns`;
      const now = new Date().toISOString();
      const existing = this.db.rawDb.prepare(
        "SELECT id FROM approach_patterns WHERE task_category = ? AND lesson = ?"
      ).get(category, lesson);
      if (!existing) {
        this.db.rawDb.prepare(`
          INSERT INTO approach_patterns
            (id, task_category, lesson, observation_count, success_rate, status, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?)
        `).run(uuidv4(), category, lesson, entries.length, 0.0, "tentative", now, now);
        processed++;
      } else {
        this.db.rawDb.prepare(
          "UPDATE approach_patterns SET observation_count = ?, updated_at = ? WHERE task_category = ? AND lesson = ?"
        ).run(entries.length, now, category, lesson);
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
