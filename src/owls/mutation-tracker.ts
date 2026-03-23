/**
 * StackOwl — DNA Mutation Tracker
 *
 * Tracks DNA mutations with before/after outcome measurement.
 * Solves the "blind evolution" problem where mutations are LLM guesses
 * with no validation. Key capabilities:
 *
 *   1. Records each mutation batch with a snapshot of DNA before/after
 *   2. Measures post-mutation user satisfaction over subsequent sessions
 *   3. Detects oscillation (same trait flipping back and forth)
 *   4. Auto-rollback when satisfaction drops significantly
 *   5. Trend analysis to identify which mutation types work best
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { OwlDNA } from './persona.js';
import type { OwlRegistry } from './registry.js';
import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

export interface MutationRecord {
  id: string;
  owlName: string;
  generation: number;
  timestamp: string;
  /** Snapshot of DNA traits before this mutation */
  beforeSnapshot: DNASnapshot;
  /** Snapshot of DNA traits after this mutation */
  afterSnapshot: DNASnapshot;
  /** Description of what changed */
  mutations: string[];
  /** User satisfaction signal gathered AFTER this mutation (initially null) */
  postMutationSatisfaction: number | null;
  /** Number of sessions observed after this mutation */
  sessionsObserved: number;
  /** Whether this mutation was rolled back */
  rolledBack: boolean;
  /** Rollback reason if applicable */
  rollbackReason?: string;
}

export interface DNASnapshot {
  challengeLevel: string;
  verbosity: string;
  humor: number;
  formality: number;
  learnedPreferences: Record<string, number>;
  expertiseGrowth: Record<string, number>;
  adviceAcceptedRate: number;
}

export interface OscillationDetection {
  isOscillating: boolean;
  oscillatingTraits: string[];
  recommendation: string;
}

export interface MutationAnalysis {
  totalMutations: number;
  avgSatisfaction: number;
  oscillations: OscillationDetection;
  bestMutationType: string | null;
  worstMutationType: string | null;
  recommendedAction: 'proceed' | 'freeze' | 'rollback';
}

// ─── Tracker ─────────────────────────────────────────────────────

export class MutationTracker {
  private records: MutationRecord[] = [];
  private filePath: string;

  /** Rolling window for satisfaction signal aggregation */
  private static readonly SATISFACTION_WINDOW = 5;
  /** Drop threshold: if satisfaction drops by this amount, trigger rollback */
  private static readonly ROLLBACK_THRESHOLD = -0.15;
  /** Minimum sessions to observe before making a judgment */
  private static readonly MIN_OBSERVATION_SESSIONS = 3;
  /** Maximum records to keep */
  private static readonly MAX_RECORDS = 50;

  constructor(
    private owlRegistry: OwlRegistry,
    workspacePath: string,
  ) {
    const trackerDir = join(workspacePath, 'evolution');
    this.filePath = join(trackerDir, 'mutation-tracker.json');
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  async init(): Promise<void> {
    const dir = join(this.filePath, '..');
    if (!existsSync(dir)) {
      await mkdir(dir, { recursive: true });
    }

    if (existsSync(this.filePath)) {
      try {
        const raw = await readFile(this.filePath, 'utf-8');
        this.records = JSON.parse(raw);
      } catch (err) {
        log.evolution.warn(`[MutationTracker] Failed to load: ${err}`);
        this.records = [];
      }
    }
  }

  private async save(): Promise<void> {
    // Cap records
    if (this.records.length > MutationTracker.MAX_RECORDS) {
      this.records = this.records.slice(-MutationTracker.MAX_RECORDS);
    }
    await writeFile(this.filePath, JSON.stringify(this.records, null, 2), 'utf-8');
  }

  // ─── Record Mutation ───────────────────────────────────────────

  /**
   * Record a mutation BEFORE it's applied. Returns a record ID.
   * Call `confirmMutation()` after applying the DNA changes.
   */
  recordBeforeMutation(owlName: string, dna: OwlDNA): string {
    const id = `mut_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

    const record: MutationRecord = {
      id,
      owlName,
      generation: dna.generation,
      timestamp: new Date().toISOString(),
      beforeSnapshot: this.snapshot(dna),
      afterSnapshot: this.snapshot(dna), // Will be updated in confirmMutation
      mutations: [],
      postMutationSatisfaction: null,
      sessionsObserved: 0,
      rolledBack: false,
    };

    this.records.push(record);
    return id;
  }

  /**
   * Confirm the mutation after DNA changes have been applied.
   */
  async confirmMutation(
    recordId: string,
    dna: OwlDNA,
    mutations: string[],
  ): Promise<void> {
    const record = this.records.find(r => r.id === recordId);
    if (!record) return;

    record.afterSnapshot = this.snapshot(dna);
    record.mutations = mutations;
    record.generation = dna.generation;

    await this.save();
    log.evolution.info(`[MutationTracker] Recorded mutation ${recordId}: ${mutations.length} changes`);
  }

  // ─── Satisfaction Feedback ─────────────────────────────────────

  /**
   * Record user satisfaction signal for the current generation.
   * Call this after each session with sentiment data from MicroLearner.
   *
   * @param satisfaction 0-1 scale (positive signals / total signals)
   */
  async recordSatisfaction(
    owlName: string,
    satisfaction: number,
  ): Promise<{ shouldRollback: boolean; rollbackGeneration?: number }> {
    await this.init();

    // Find the most recent unresolved mutation for this owl
    const recent = [...this.records]
      .reverse()
      .find(r => r.owlName === owlName && !r.rolledBack && r.postMutationSatisfaction === null);

    if (!recent) {
      return { shouldRollback: false };
    }

    recent.sessionsObserved++;

    // Running average of satisfaction
    if (recent.postMutationSatisfaction === null) {
      recent.postMutationSatisfaction = satisfaction;
    } else {
      recent.postMutationSatisfaction =
        (recent.postMutationSatisfaction * (recent.sessionsObserved - 1) + satisfaction) /
        recent.sessionsObserved;
    }

    await this.save();

    // Check if we have enough data for a judgment
    if (recent.sessionsObserved < MutationTracker.MIN_OBSERVATION_SESSIONS) {
      return { shouldRollback: false };
    }

    // Compare with pre-mutation satisfaction baseline
    const baseline = this.getBaseline(owlName, recent.id);
    const delta = (recent.postMutationSatisfaction ?? 0) - baseline;

    if (delta < MutationTracker.ROLLBACK_THRESHOLD) {
      log.evolution.warn(
        `[MutationTracker] Satisfaction dropped ${(delta * 100).toFixed(0)}% after mutation ${recent.id} — recommending rollback`,
      );
      return { shouldRollback: true, rollbackGeneration: recent.generation };
    }

    return { shouldRollback: false };
  }

  // ─── Rollback ──────────────────────────────────────────────────

  /**
   * Roll back a mutation by restoring the before-snapshot DNA state.
   */
  async rollback(owlName: string, recordId: string): Promise<boolean> {
    const record = this.records.find(r => r.id === recordId);
    if (!record || record.rolledBack) return false;

    const owl = this.owlRegistry.get(owlName);
    if (!owl) return false;

    // Restore pre-mutation state
    const before = record.beforeSnapshot;
    owl.dna.evolvedTraits.challengeLevel = before.challengeLevel as OwlDNA['evolvedTraits']['challengeLevel'];
    owl.dna.evolvedTraits.verbosity = before.verbosity as OwlDNA['evolvedTraits']['verbosity'];
    owl.dna.evolvedTraits.humor = before.humor;
    owl.dna.evolvedTraits.formality = before.formality;

    // Restore preferences (only those that changed)
    for (const [key, value] of Object.entries(before.learnedPreferences)) {
      owl.dna.learnedPreferences[key] = value;
    }

    // Restore expertise (only those that changed)
    for (const [key, value] of Object.entries(before.expertiseGrowth)) {
      owl.dna.expertiseGrowth[key] = value;
    }

    record.rolledBack = true;
    record.rollbackReason =
      `Satisfaction dropped to ${((record.postMutationSatisfaction ?? 0) * 100).toFixed(0)}% ` +
      `(baseline was ${(this.getBaseline(owlName, recordId) * 100).toFixed(0)}%)`;

    await this.owlRegistry.saveDNA(owlName);
    await this.save();

    log.evolution.info(
      `[MutationTracker] ✅ Rolled back mutation ${recordId} for ${owlName}: ${record.rollbackReason}`,
    );

    return true;
  }

  // ─── Analysis ──────────────────────────────────────────────────

  /**
   * Analyze mutation history to detect oscillation and recommend action.
   * Call BEFORE applying a new mutation to decide if the owl should
   * evolve or freeze.
   */
  analyze(owlName: string): MutationAnalysis {
    const owlRecords = this.records.filter(r => r.owlName === owlName && !r.rolledBack);

    if (owlRecords.length < 3) {
      return {
        totalMutations: owlRecords.length,
        avgSatisfaction: 0.5,
        oscillations: { isOscillating: false, oscillatingTraits: [], recommendation: '' },
        bestMutationType: null,
        worstMutationType: null,
        recommendedAction: 'proceed',
      };
    }

    // Calculate average satisfaction
    const withSatisfaction = owlRecords.filter(r => r.postMutationSatisfaction !== null);
    const avgSatisfaction = withSatisfaction.length > 0
      ? withSatisfaction.reduce((sum, r) => sum + (r.postMutationSatisfaction ?? 0), 0) / withSatisfaction.length
      : 0.5;

    // Detect oscillation
    const oscillations = this.detectOscillation(owlRecords);

    // Find best/worst mutation types
    const mutationTypes = new Map<string, number[]>();
    for (const record of withSatisfaction) {
      for (const mut of record.mutations) {
        const type = this.classifyMutation(mut);
        const existing = mutationTypes.get(type) ?? [];
        existing.push(record.postMutationSatisfaction ?? 0.5);
        mutationTypes.set(type, existing);
      }
    }

    let bestType: string | null = null;
    let worstType: string | null = null;
    let bestAvg = -1;
    let worstAvg = 2;

    for (const [type, scores] of mutationTypes) {
      const avg = scores.reduce((s, v) => s + v, 0) / scores.length;
      if (avg > bestAvg) { bestAvg = avg; bestType = type; }
      if (avg < worstAvg) { worstAvg = avg; worstType = type; }
    }

    // Recommended action
    let action: MutationAnalysis['recommendedAction'] = 'proceed';
    if (oscillations.isOscillating) action = 'freeze';
    if (avgSatisfaction < 0.3) action = 'rollback';

    return {
      totalMutations: owlRecords.length,
      avgSatisfaction,
      oscillations,
      bestMutationType: bestType,
      worstMutationType: worstType,
      recommendedAction: action,
    };
  }

  // ─── Private Helpers ───────────────────────────────────────────

  private snapshot(dna: OwlDNA): DNASnapshot {
    return {
      challengeLevel: dna.evolvedTraits.challengeLevel,
      verbosity: dna.evolvedTraits.verbosity,
      humor: dna.evolvedTraits.humor,
      formality: dna.evolvedTraits.formality,
      learnedPreferences: { ...dna.learnedPreferences },
      expertiseGrowth: { ...dna.expertiseGrowth },
      adviceAcceptedRate: dna.interactionStats.adviceAcceptedRate,
    };
  }

  private getBaseline(owlName: string, excludeRecordId: string): number {
    const previous = this.records
      .filter(r => r.owlName === owlName && r.id !== excludeRecordId && r.postMutationSatisfaction !== null)
      .slice(-MutationTracker.SATISFACTION_WINDOW);

    if (previous.length === 0) return 0.5; // Neutral baseline

    return previous.reduce((sum, r) => sum + (r.postMutationSatisfaction ?? 0.5), 0) / previous.length;
  }

  private detectOscillation(records: MutationRecord[]): OscillationDetection {
    const last5 = records.slice(-5);
    if (last5.length < 3) {
      return { isOscillating: false, oscillatingTraits: [], recommendation: '' };
    }

    const oscillating: string[] = [];

    // Check challenge level oscillation
    const challengeLevels = last5.map(r => r.afterSnapshot.challengeLevel);
    if (this.isFlipping(challengeLevels)) oscillating.push('challengeLevel');

    // Check verbosity oscillation
    const verbosities = last5.map(r => r.afterSnapshot.verbosity);
    if (this.isFlipping(verbosities)) oscillating.push('verbosity');

    // Check humor oscillation (continuous value)
    const humors = last5.map(r => r.afterSnapshot.humor);
    if (this.isOscillatingNumeric(humors)) oscillating.push('humor');

    return {
      isOscillating: oscillating.length > 0,
      oscillatingTraits: oscillating,
      recommendation: oscillating.length > 0
        ? `FREEZE these traits for 2 weeks: ${oscillating.join(', ')}. The LLM is flip-flopping — user signals are contradictory.`
        : '',
    };
  }

  private isFlipping(values: string[]): boolean {
    if (values.length < 3) return false;
    let flips = 0;
    for (let i = 2; i < values.length; i++) {
      if (values[i] === values[i - 2] && values[i] !== values[i - 1]) {
        flips++;
      }
    }
    return flips >= 1; // At least one A→B→A pattern
  }

  private isOscillatingNumeric(values: number[]): boolean {
    if (values.length < 3) return false;
    let directionChanges = 0;
    for (let i = 2; i < values.length; i++) {
      const prev = values[i - 1] - values[i - 2];
      const curr = values[i] - values[i - 1];
      if ((prev > 0.05 && curr < -0.05) || (prev < -0.05 && curr > 0.05)) {
        directionChanges++;
      }
    }
    return directionChanges >= 2;
  }

  private classifyMutation(mutation: string): string {
    const lower = mutation.toLowerCase();
    if (lower.includes('verbosity')) return 'verbosity';
    if (lower.includes('challenge')) return 'challenge';
    if (lower.includes('preference')) return 'preference';
    if (lower.includes('expertise')) return 'expertise';
    if (lower.includes('humor')) return 'humor';
    if (lower.includes('formality')) return 'formality';
    return 'other';
  }
}
