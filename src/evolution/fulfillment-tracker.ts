/**
 * StackOwl — Fulfillment Tracker
 *
 * Closes the DNA feedback loop: when a desire is fulfilled (researched,
 * pellet saved), the owl's DNA intensity for that domain is reinforced.
 *
 * Also tracks unfulfilled desires over time so the CognitiveLoop can
 * deprioritize recurring failures and surface new ones.
 *
 * Persisted to JSON alongside the owl's DNA file.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { log } from "../logger.js";
import type { OwlDesire } from "../owls/inner-life.js";
import type { DesireExecutionResult } from "./desire-executor.js";

// ─── Types ────────────────────────────────────────────────────────

export interface FulfillmentRecord {
  desireDescription: string;
  fulfilledAt: string;       // ISO date
  pelletTitle: string;
  impactScore: number;       // 0–1, how much this reinforced DNA
  owlName: string;
}

export interface DNAImpact {
  domain: string;
  delta: number;             // positive = reinforce, negative = decay
  reason: string;
}

interface TrackerState {
  records: FulfillmentRecord[];
  failureCounts: Record<string, number>; // desire description → fail count
  lastUpdated: string;
}

// ─── FulfillmentTracker ───────────────────────────────────────────

export class FulfillmentTracker {
  private state: TrackerState = {
    records: [],
    failureCounts: {},
    lastUpdated: new Date().toISOString(),
  };
  private loaded = false;

  constructor(private storePath: string) {}

  /**
   * Record that a desire was fulfilled. Returns the DNA impact.
   */
  async recordFulfillment(
    result: DesireExecutionResult,
    owlName: string,
  ): Promise<DNAImpact> {
    await this.ensureLoaded();

    const record: FulfillmentRecord = {
      desireDescription: result.desire.description,
      fulfilledAt: new Date().toISOString(),
      pelletTitle: result.pelletTitle,
      impactScore: this.computeImpact(result),
      owlName,
    };

    this.state.records.push(record);
    // Clear failure count on success
    delete this.state.failureCounts[result.desire.description];
    this.state.lastUpdated = new Date().toISOString();

    await this.save();

    const domain = this.extractDomain(result.desire.description);
    const impact: DNAImpact = {
      domain,
      delta: record.impactScore * 0.05, // small positive nudge to DNA
      reason: `Desire fulfilled: "${result.desire.description.slice(0, 60)}"`,
    };

    log.engine.info(
      `[FulfillmentTracker] Recorded fulfillment for ${owlName}: ` +
      `domain="${domain}" delta=+${impact.delta.toFixed(3)}`,
    );

    return impact;
  }

  /**
   * Record that a desire execution attempt failed.
   * Repeated failures reduce the desire's effective priority.
   */
  async recordFailure(desire: OwlDesire): Promise<void> {
    await this.ensureLoaded();
    const key = desire.description;
    this.state.failureCounts[key] = (this.state.failureCounts[key] ?? 0) + 1;
    this.state.lastUpdated = new Date().toISOString();
    await this.save();
  }

  /**
   * Return desires sorted by adjusted priority
   * (intensity minus failure penalty).
   */
  async prioritize(desires: OwlDesire[]): Promise<OwlDesire[]> {
    await this.ensureLoaded();

    return [...desires].sort((a, b) => {
      const aAdj = a.intensity - this.failurePenalty(a.description);
      const bAdj = b.intensity - this.failurePenalty(b.description);
      return bAdj - aAdj;
    });
  }

  /**
   * Recent fulfillment records, newest first.
   */
  async getHistory(limit = 20): Promise<FulfillmentRecord[]> {
    await this.ensureLoaded();
    return [...this.state.records]
      .sort((a, b) => b.fulfilledAt.localeCompare(a.fulfilledAt))
      .slice(0, limit);
  }

  // ─── Private ─────────────────────────────────────────────────

  private computeImpact(result: DesireExecutionResult): number {
    let score = result.desire.intensity;
    if (result.pelletSaved) score = Math.min(1, score + 0.2);
    if (result.research.length > 500) score = Math.min(1, score + 0.1);
    return score;
  }

  private failurePenalty(description: string): number {
    const count = this.state.failureCounts[description] ?? 0;
    return Math.min(0.5, count * 0.1); // max -0.5
  }

  private extractDomain(description: string): string {
    const domainKeywords: Record<string, RegExp> = {
      systems:     /\b(?:system|architecture|distributed|concurrent|async)\b/i,
      coding:      /\b(?:code|programming|algorithm|language|library|framework)\b/i,
      philosophy:  /\b(?:philosophy|ethics|consciousness|meaning|existence)\b/i,
      science:     /\b(?:science|physics|biology|chemistry|mathematics|math)\b/i,
      creativity:  /\b(?:creative|art|design|writing|music|story)\b/i,
      language:    /\b(?:language|linguistic|word|grammar|semantic)\b/i,
    };

    for (const [domain, re] of Object.entries(domainKeywords)) {
      if (re.test(description)) return domain;
    }
    return "general";
  }

  private async ensureLoaded(): Promise<void> {
    if (this.loaded) return;

    try {
      const raw = await readFile(this.storePath, "utf-8");
      this.state = JSON.parse(raw) as TrackerState;
    } catch {
      // File doesn't exist yet — use defaults
    }

    this.loaded = true;
  }

  private async save(): Promise<void> {
    try {
      await mkdir(dirname(this.storePath), { recursive: true });
      await writeFile(this.storePath, JSON.stringify(this.state, null, 2), "utf-8");
    } catch (err) {
      log.engine.warn(`[FulfillmentTracker] Failed to save: ${err instanceof Error ? err.message : err}`);
    }
  }
}
