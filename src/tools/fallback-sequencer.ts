/**
 * StackOwl — Fallback Sequencer
 *
 * Applies learned fallback sequences when tools fail, not static ones.
 * Works with FallbackDiscoverer to improve recovery strategies over time.
 */

import { log } from "../logger.js";

export interface FallbackSequence {
  toolName: string;
  fallbackOrder: string[];
  learnedFrom: "static" | "discovered";
}

export interface FallbackOutcome {
  sequence: string[];
  success: boolean;
  failureReason?: string;
}

export class FallbackSequencer {
  private learnedSequences: Map<string, FallbackSequence> = new Map();
  private outcomeHistory: Map<string, FallbackOutcome[]> = new Map();

  private key(owlName: string, toolName: string, taskType: string): string {
    return `${owlName}::${toolName}::${taskType}`;
  }

  recordFallbackOutcome(
    owlName: string,
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
    failureReason?: string,
  ): void {
    const k = this.key(owlName, toolName, taskType);
    const outcomes = this.outcomeHistory.get(k) ?? [];
    outcomes.push({ sequence, success, failureReason });
    this.outcomeHistory.set(k, outcomes);

    this.updateLearnedSequence(owlName, toolName, taskType, sequence, success);
  }

  private updateLearnedSequence(
    owlName: string,
    toolName: string,
    taskType: string,
    sequence: string[],
    success: boolean,
  ): void {
    const k = this.key(owlName, toolName, taskType);
    const existing = this.learnedSequences.get(k);

    if (!existing || success) {
      this.learnedSequences.set(k, {
        toolName,
        fallbackOrder: sequence,
        learnedFrom: "discovered",
      });

      log.engine.debug(
        `[FallbackSequencer] Learned new sequence for ${toolName}: ${sequence.join(" -> ")}`,
      );
    }
  }

  getFallbackSequence(
    owlName: string,
    toolName: string,
    taskType: string,
  ): string[] {
    const k = this.key(owlName, toolName, taskType);
    const learned = this.learnedSequences.get(k);

    if (learned) {
      log.engine.debug(
        `[FallbackSequencer] Using learned sequence for ${toolName}: ${learned.fallbackOrder.join(" -> ")}`,
      );
      return learned.fallbackOrder;
    }

    return this.getDefaultSequence(toolName);
  }

  private getDefaultSequence(toolName: string): string[] {
    const defaults: Record<string, string[]> = {
      web_fetch: ["web_search", "pellet_recall", "recall"],
      read_file: ["shell", "pellet_recall", "recall"],
      write_file: ["shell", "remember"],
      shell: ["read_file", "recall"],
      default: ["recall", "remember", "pellet_recall"],
    };

    return defaults[toolName] ?? defaults["default"];
  }

  getLearnedSequence(
    owlName: string,
    toolName: string,
    taskType: string,
  ): FallbackSequence | undefined {
    const k = this.key(owlName, toolName, taskType);
    return this.learnedSequences.get(k);
  }

  getOutcomeHistory(
    owlName: string,
    toolName: string,
    taskType: string,
  ): FallbackOutcome[] {
    const k = this.key(owlName, toolName, taskType);
    return this.outcomeHistory.get(k) ?? [];
  }
}
