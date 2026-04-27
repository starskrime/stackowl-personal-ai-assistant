/**
 * StackOwl — Fallback Discoverer
 *
 * Discovers and records new fallback paths when existing ones fail.
 * Works with FallbackSequencer to improve recovery strategies over time.
 */

import { log } from "../logger.js";

export interface DiscoveredPath {
  sequence: string[];
  successRate: number;
  attemptCount: number;
  lastAttempt: string;
}

export class FallbackDiscoverer {
  private discoveredPaths: Map<string, DiscoveredPath[]> = new Map();

  private key(toolName: string, taskType: string): string {
    return `${toolName}::${taskType}`;
  }

  recordAttempt(
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
  ): void {
    const k = this.key(toolName, taskType);
    const paths = this.discoveredPaths.get(k) ?? [];

    const existingPath = paths.find(
      (p) => p.sequence.join("->") === sequence.join("->"),
    );

    if (existingPath) {
      existingPath.attemptCount++;
      existingPath.successRate = success
        ? (existingPath.successRate * (existingPath.attemptCount - 1) + 1) /
          existingPath.attemptCount
        : (existingPath.successRate * (existingPath.attemptCount - 1)) /
          existingPath.attemptCount;
      existingPath.lastAttempt = new Date().toISOString();
    } else {
      paths.push({
        sequence: [...sequence],
        successRate: success ? 1 : 0,
        attemptCount: 1,
        lastAttempt: new Date().toISOString(),
      });
    }

    this.discoveredPaths.set(k, paths);

    log.engine.debug(
      `[FallbackDiscoverer] Recorded attempt for ${toolName}/${taskType}: ${sequence.join(" -> ")} (success=${success})`,
    );
  }

  getBestPath(toolName: string, taskType: string): string[] | null {
    const k = this.key(toolName, taskType);
    const paths = this.discoveredPaths.get(k);

    if (!paths || paths.length === 0) return null;

    const sorted = [...paths].sort((a, b) => {
      const aReliable = a.attemptCount >= 3;
      const bReliable = b.attemptCount >= 3;

      if (aReliable && !bReliable) return -1;
      if (!aReliable && bReliable) return 1;

      if (aReliable && bReliable) {
        return b.successRate - a.successRate;
      }

      if (b.successRate !== a.successRate) {
        return b.successRate - a.successRate;
      }

      return b.attemptCount - a.attemptCount;
    });

    return sorted[0].sequence;
  }

  getAllPaths(toolName: string, taskType: string): DiscoveredPath[] {
    const k = this.key(toolName, taskType);
    return this.discoveredPaths.get(k) ?? [];
  }

  getMostReliablePath(
    toolName: string,
    taskType: string,
    minAttempts = 3,
  ): string[] | null {
    const k = this.key(toolName, taskType);
    const paths = this.discoveredPaths.get(k);

    if (!paths || paths.length === 0) return null;

    const reliable = paths
      .filter((p) => p.attemptCount >= minAttempts)
      .sort((a, b) => b.successRate - a.successRate);

    return reliable.length > 0 ? reliable[0].sequence : null;
  }
}
