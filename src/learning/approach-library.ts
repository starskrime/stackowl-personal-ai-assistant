/**
 * StackOwl — Approach Library
 *
 * Records what worked and what failed for (owlId, toolName, taskType) triples,
 * computes effectiveness scores with recency decay, and provides retrieval
 * for the tool selection weight system.
 *
 * This is the persistence layer — the actual tool selection weight influence
 * happens in the router, which consults this library before choosing a tool.
 */

import { log } from "../logger.js";

export interface ApproachPattern {
  owlName: string;
  toolName: string;
  taskType: string;
  successCount: number;
  failureCount: number;
  effectivenessScore: number;
  lastSuccessAt?: string;
  lastFailureAt?: string;
  createdAt: string;
  updatedAt: string;
}

export interface PatternRecord {
  id: string;
  owlName: string;
  toolName: string;
  taskKeywords: string;
  argsSummary: string;
  outcome: "success" | "failure";
  failureReason?: string;
  createdAt: string;
}

const DECAY_HALFLIFE_DAYS = 14;
const MIN_EFFECTIVENESS = 0.1;
const MAX_EFFECTIVENESS = 0.95;

function computeDecayFactor(daysElapsed: number): number {
  return Math.pow(0.5, daysElapsed / DECAY_HALFLIFE_DAYS);
}

export class ApproachLibrary {
  private patterns: Map<string, ApproachPattern> = new Map();
  private records: PatternRecord[] = [];

  private key(owlName: string, toolName: string, taskType: string): string {
    return `${owlName}::${toolName}::${taskType}`;
  }

  recordOutcome(
    owlName: string,
    toolName: string,
    taskType: string,
    outcome: "success" | "failure",
    opts?: { argsSummary?: string; failureReason?: string },
  ): void {
    const k = this.key(owlName, toolName, taskType);
    let pattern = this.patterns.get(k);

    if (!pattern) {
      pattern = {
        owlName,
        toolName,
        taskType,
        successCount: 0,
        failureCount: 0,
        effectivenessScore: 0.5,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      this.patterns.set(k, pattern);
    }

    const now = new Date();

    if (outcome === "success") {
      pattern.successCount++;
      pattern.lastSuccessAt = now.toISOString();
    } else {
      pattern.failureCount++;
      pattern.lastFailureAt = now.toISOString();
    }

    pattern.effectivenessScore = this.calculateEffectiveness(pattern, now);
    pattern.updatedAt = now.toISOString();

    const record: PatternRecord = {
      id: `appr_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      owlName,
      toolName,
      taskKeywords: taskType,
      argsSummary: opts?.argsSummary ?? "",
      outcome,
      failureReason: opts?.failureReason,
      createdAt: now.toISOString(),
    };
    this.records.push(record);

    log.engine.debug(
      `[ApproachLibrary] Recorded ${outcome} for ${owlName}/${toolName}/${taskType} → effectiveness=${pattern.effectivenessScore.toFixed(3)}`,
    );
  }

  private calculateEffectiveness(pattern: ApproachPattern, now: Date): number {
    const total = pattern.successCount + pattern.failureCount;
    if (total === 0) return 0.5;

    const baseScore = pattern.successCount / total;

    const lastTime = pattern.lastSuccessAt
      ? new Date(pattern.lastSuccessAt)
      : pattern.lastFailureAt
        ? new Date(pattern.lastFailureAt)
        : now;

    const daysElapsed = (now.getTime() - lastTime.getTime()) / (1000 * 60 * 60 * 24);
    const decayFactor = computeDecayFactor(daysElapsed);

    const recencyBonus = Math.min(0.15, (1 - decayFactor) * 0.15);
    const score = Math.min(MAX_EFFECTIVENESS, Math.max(MIN_EFFECTIVENESS, baseScore + recencyBonus));

    return Math.round(score * 1000) / 1000;
  }

  getPatterns(owlName: string, toolName: string, taskType: string): ApproachPattern | undefined {
    return this.patterns.get(this.key(owlName, toolName, taskType));
  }

getPatternsForTool(owlName: string, toolName: string): ApproachPattern[] {
    return Array.from(this.patterns.values()).filter((p) =>
      p.toolName === toolName && p.owlName === owlName,
    );
  }

  getEffectivenessScore(owlName: string, toolName: string, taskType: string): number {
    const pattern = this.patterns.get(this.key(owlName, toolName, taskType));
    return pattern?.effectivenessScore ?? 0.5;
  }

  getAllPatterns(): ApproachPattern[] {
    return Array.from(this.patterns.values());
  }

  getRecentRecords(owlName: string, limit = 20): PatternRecord[] {
    return this.records
      .filter((r) => r.owlName === owlName)
      .slice(-limit)
      .reverse();
  }

  getSuccessfulPatterns(owlName: string, toolName: string, taskType: string): PatternRecord[] {
    return this.records.filter(
      (r) =>
        r.owlName === owlName &&
        r.toolName === toolName &&
        r.taskKeywords === taskType &&
        r.outcome === "success",
    );
  }

  getFailedPatterns(owlName: string, toolName: string, taskType: string): PatternRecord[] {
    return this.records.filter(
      (r) =>
        r.owlName === owlName &&
        r.toolName === toolName &&
        r.taskKeywords === taskType &&
        r.outcome === "failure",
    );
  }

  toContextString(owlName: string): string {
    const relevant = Array.from(this.patterns.values()).filter(
      (p) => p.owlName === owlName && (p.successCount > 0 || p.failureCount > 0),
    );

    if (relevant.length === 0) return "";

    const lines: string[] = ["[Approach Library]"];

    for (const p of relevant) {
      const score = p.effectivenessScore;
      const scoreBar = score >= 0.7 ? "✓" : score >= 0.4 ? "~" : "✗";
      lines.push(
        `  ${scoreBar} ${p.toolName}/${p.taskType}: ` +
          `${p.successCount}✓ ${p.failureCount}✗ score=${score.toFixed(2)}`,
      );
    }

    return lines.join("\n");
  }
}