/**
 * StackOwl — Mistake Pattern Detector
 *
 * Detects when the same mistake is repeated (2+ failures with same tool + similar task type).
 * Stores patterns with high importance for evolution analysis.
 * Warns during planning to prevent repeating failed approaches.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export interface MistakePattern {
  id: string;
  toolName: string;
  taskType: string;
  failureReason: string;
  occurrences: number;
  firstSeen: string;
  lastSeen: string;
  importance: "high" | "medium" | "low";
  lastWarnedAt?: string;
}

export interface MistakePatternStore {
  patterns: MistakePattern[];
  totalDetections: number;
  lastCleanupAt: string;
}

export interface PatternWarning {
  pattern: MistakePattern;
  message: string;
  alternatives?: string[];
}

export interface ApproachRecord {
  id: string;
  owlName: string;
  toolName: string;
  taskKeywords: string;
  argsSummary: string;
  outcome: "success" | "failure";
  failureReason?: string;
  createdAt: string;
}

export interface ApproachLibrary {
  getRecentFailuresForTool(toolName: string, limit?: number): ApproachRecord[];
  getRecentFailures(owlName: string, limit?: number): ApproachRecord[];
  getRecentSuccesses(toolName: string, limit?: number): ApproachRecord[];
}

const PATTERNS_FILE = "mistake_patterns.json";
const SIMILARITY_THRESHOLD = 0.6;
const MAX_PATTERNS = 100;
const WARNING_COOLDOWN_MS = 60 * 60 * 1000;

function generateId(): string {
  return `mp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function computeSimilarity(a: string, b: string): number {
  const aWords = new Set(
    a.toLowerCase().split(/\s+/).filter((w) => w.length >= 3),
  );
  const bWords = new Set(
    b.toLowerCase().split(/\s+/).filter((w) => w.length >= 3),
  );
  if (aWords.size === 0 || bWords.size === 0) return 0;
  const intersection = [...aWords].filter((w) => bWords.has(w));
  const union = new Set([...aWords, ...bWords]);
  return intersection.length / union.size;
}

export class MistakePatternDetector {
  private store: MistakePatternStore;
  private patternsPath: string;
  private approachLibrary: ApproachLibrary | null = null;

  constructor(
    private workspacePath: string,
    approachLibrary?: ApproachLibrary,
    _owlName = "default",
  ) {
    this.patternsPath = join(workspacePath, PATTERNS_FILE);
    this.approachLibrary = approachLibrary ?? null;
    this.store = {
      patterns: [],
      totalDetections: 0,
      lastCleanupAt: new Date().toISOString(),
    };
  }

  /**
   * Set the approach library for reading recent failures/successes.
   */
  setApproachLibrary(library: ApproachLibrary): void {
    this.approachLibrary = library;
  }

  /**
   * Clear all patterns (for testing purposes only).
   */
  clearForTesting(): void {
    this.store.patterns = [];
    this.store.totalDetections = 0;
  }

  /**
   * Record a tool execution failure.
   * Checks recent history for similar failures and flags repeat patterns.
   */
  recordFailure(
    toolName: string,
    taskType: string,
    failureReason: string,
  ): MistakePattern | null {
    const now = new Date().toISOString();

    const similarCount = this.countSimilarRecentFailures(
      toolName,
      taskType,
      failureReason,
    );

    if (similarCount > 0) {
      const existing = this.findPattern(toolName, taskType, failureReason);
      if (existing) {
        existing.occurrences++;
        existing.lastSeen = now;
        if (existing.occurrences >= 2) {
          existing.importance = "high";
          log.evolution.evolve(
            `behavioral.evolution.mistake_pattern_detected ` +
              `tool=${toolName} pattern="${failureReason.slice(0, 60)}..." occurrences=${existing.occurrences}`,
          );
          this.store.totalDetections++;
          this.persist();
          return existing;
        }
      } else {
        const newPattern: MistakePattern = {
          id: generateId(),
          toolName,
          taskType,
          failureReason: failureReason.slice(0, 400),
          occurrences: 1,
          firstSeen: now,
          lastSeen: now,
          importance: "medium",
        };
        this.store.patterns.push(newPattern);
        this.persist();
      }
    } else {
      const newPattern: MistakePattern = {
        id: generateId(),
        toolName,
        taskType,
        failureReason: failureReason.slice(0, 400),
        occurrences: 1,
        firstSeen: now,
        lastSeen: now,
        importance: "low",
      };
      this.store.patterns.push(newPattern);
      this.persist();
    }

    return null;
  }

  /**
   * Count how many similar failures exist in recent history (from ApproachLibrary).
   */
  private countSimilarRecentFailures(
    toolName: string,
    taskType: string,
    failureReason: string,
  ): number {
    if (!this.approachLibrary) return 0;

    const recent = this.approachLibrary.getRecentFailuresForTool(toolName, 10);
    let count = 0;

    for (const record of recent) {
      const taskSimilarity = computeSimilarity(record.taskKeywords, taskType);
      const reasonSimilarity = computeSimilarity(
        record.failureReason ?? "",
        failureReason,
      );

      if (
        taskSimilarity >= SIMILARITY_THRESHOLD &&
        reasonSimilarity >= SIMILARITY_THRESHOLD * 0.8
      ) {
        count++;
      }
    }

    return count;
  }

  /**
   * Find an existing pattern matching tool + task + reason.
   */
  private findPattern(
    toolName: string,
    taskType: string,
    failureReason: string,
  ): MistakePattern | undefined {
    return this.store.patterns.find(
      (p) =>
        p.toolName === toolName &&
        computeSimilarity(p.taskType, taskType) >= SIMILARITY_THRESHOLD &&
        computeSimilarity(p.failureReason, failureReason) >= SIMILARITY_THRESHOLD * 0.8,
    );
  }

  /**
   * Check if a task is about to repeat a known mistake pattern.
   * Returns a warning with alternatives if a pattern is found.
   */
  warnForTask(
    toolName: string,
    taskType: string,
    taskKeywords: string,
  ): PatternWarning | null {
    const now = Date.now();

    const matchingPatterns = this.store.patterns.filter(
      (p) =>
        p.toolName === toolName &&
        p.importance === "high" &&
        computeSimilarity(p.taskType, taskKeywords) >= SIMILARITY_THRESHOLD,
    );

    if (matchingPatterns.length === 0) return null;

    const pattern = matchingPatterns[0];

    if (pattern.lastWarnedAt) {
      const lastWarned = new Date(pattern.lastWarnedAt).getTime();
      if (now - lastWarned < WARNING_COOLDOWN_MS) {
        return null;
      }
    }

    pattern.lastWarnedAt = new Date(now).toISOString();

    const alternatives = this.getAlternativeApproaches(toolName, taskKeywords);

    const warningMsg =
      `⚠️ Repeat mistake detected: "${pattern.failureReason.slice(0, 80)}" ` +
      `has failed ${pattern.occurrences}x for ${toolName}/${pattern.taskType}. ` +
      (alternatives.length > 0
        ? `Previous successes with ${toolName}: ${alternatives.join("; ")}`
        : "No known alternative approaches.");

    log.evolution.evolve(
      `[MistakeDetector] Warning for ${toolName}/${taskType}: ` +
        `${pattern.occurrences}x failure pattern — suggesting alternatives`,
    );

    return {
      pattern,
      message: warningMsg,
      alternatives: alternatives.length > 0 ? alternatives : undefined,
    };
  }

  /**
   * Get alternative successful approaches for a tool.
   */
  private getAlternativeApproaches(
    toolName: string,
    taskKeywords: string,
  ): string[] {
    if (!this.approachLibrary) return [];

    const successes = this.approachLibrary.getRecentSuccesses(toolName, 3);
    return successes
      .filter((s) => computeSimilarity(s.taskKeywords, taskKeywords) >= 0.3)
      .map((s) => `${s.taskKeywords} → ${s.argsSummary}`.slice(0, 100))
      .slice(0, 3);
  }

  /**
   * Get all high-importance patterns for evolution analysis.
   */
  getHighImportancePatterns(): MistakePattern[] {
    return this.store.patterns
      .filter((p) => p.importance === "high")
      .sort((a, b) => b.occurrences - a.occurrences);
  }

  /**
   * Get patterns suitable for system prompt injection.
   */
  getPatternsForPrompt(maxPatterns = 5): MistakePattern[] {
    return this.store.patterns
      .filter((p) => p.importance === "high")
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, maxPatterns);
  }

  /**
   * Get statistics about detected patterns.
   */
  getStats(): {
    totalPatterns: number;
    highImportance: number;
    totalDetections: number;
    topPatterns: { toolName: string; occurrences: number }[];
  } {
    const highImportance = this.store.patterns.filter(
      (p) => p.importance === "high",
    ).length;

    const topPatterns = this.store.patterns
      .filter((p) => p.importance === "high")
      .sort((a, b) => b.occurrences - a.occurrences)
      .slice(0, 5)
      .map((p) => ({ toolName: p.toolName, occurrences: p.occurrences }));

    return {
      totalPatterns: this.store.patterns.length,
      highImportance,
      totalDetections: this.store.totalDetections,
      topPatterns,
    };
  }

  private async persist(): Promise<void> {
    try {
      if (!existsSync(this.workspacePath)) {
        await mkdir(this.workspacePath, { recursive: true });
      }
      await writeFile(
        this.patternsPath,
        JSON.stringify(this.store, null, 2),
        "utf-8",
      );
    } catch (err) {
      log.evolution.warn(
        `[MistakeDetector] Failed to persist patterns: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  async load(): Promise<void> {
    if (!existsSync(this.patternsPath)) return;
    try {
      const raw = await readFile(this.patternsPath, "utf-8");
      const data = JSON.parse(raw) as MistakePatternStore;
      this.store = {
        patterns: Array.isArray(data.patterns) ? data.patterns : [],
        totalDetections: data.totalDetections ?? 0,
        lastCleanupAt: data.lastCleanupAt ?? new Date().toISOString(),
      };
    } catch (err) {
      log.evolution.warn(
        `[MistakeDetector] Failed to load patterns: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Prune old patterns to keep the store manageable.
   */
  async cleanup(): Promise<number> {
    const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
    const before = this.store.patterns.length;

    this.store.patterns = this.store.patterns.filter((p) => {
      if (p.importance === "high") return true;
      const lastSeen = new Date(p.lastSeen).getTime();
      return lastSeen > cutoff;
    });

    if (this.store.patterns.length > MAX_PATTERNS) {
      const lowImportance = this.store.patterns
        .filter((p) => p.importance !== "high")
        .sort(
          (a, b) =>
            new Date(a.lastSeen).getTime() - new Date(b.lastSeen).getTime(),
        );

      const toRemove = this.store.patterns.length - MAX_PATTERNS;
      const removeIds = new Set(lowImportance.slice(0, toRemove).map((p) => p.id));
      this.store.patterns = this.store.patterns.filter((p) => !removeIds.has(p.id));
    }

    this.store.lastCleanupAt = new Date().toISOString();
    await this.persist();

    return before - this.store.patterns.length;
  }
}