/**
 * StackOwl — Memory Feedback
 *
 * Phase 5 of the Mem0-inspired memory layer.
 * Handles user corrections, confirmations, and TTL management
 * for the fact store and episodic memory.
 *
 * Feedback types:
 *   - CORRECTION: User says a fact is wrong → retire + optionally add corrected fact
 *   - CONFIRMATION: User confirms a fact is correct → bump confidence
 *   - CONTRADICTION: User provides contradicting info → store both + graph edge
 *   - REFINEMENT: User provides more precise version → update fact text
 */

import type { FactStore, StoredFact } from "./fact-store.js";
import type { KnowledgeGraph } from "../knowledge/graph.js";
import { log } from "../logger.js";

export type FeedbackType =
  | "CORRECTION"
  | "CONFIRMATION"
  | "CONTRADICTION"
  | "REFINEMENT";

export interface FeedbackEvent {
  type: FeedbackType;
  factId?: string;
  userId: string;
  message: string;
  correctedFact?: string;
  newCategory?: string;
  timestamp: string;
}

export class MemoryFeedback {
  constructor(
    private factStore: FactStore,
    private knowledgeGraph?: KnowledgeGraph,
  ) {}

  /**
   * Process a user feedback event.
   * Dispatches to the appropriate handler based on feedback type.
   */
  async process(
    event: FeedbackEvent,
  ): Promise<{ success: boolean; message: string }> {
    log.memory.info(
      `[MemoryFeedback] ${event.type} from ${event.userId}: ${event.message.slice(0, 80)}`,
    );

    switch (event.type) {
      case "CONFIRMATION":
        return this.handleConfirmation(event);
      case "CORRECTION":
        return this.handleCorrection(event);
      case "CONTRADICTION":
        return this.handleContradiction(event);
      case "REFINEMENT":
        return this.handleRefinement(event);
      default:
        return {
          success: false,
          message: `Unknown feedback type: ${event.type}`,
        };
    }
  }

  /**
   * User confirms a fact is correct — bump confidence.
   */
  private async handleConfirmation(
    event: FeedbackEvent,
  ): Promise<{ success: boolean; message: string }> {
    if (!event.factId) {
      return { success: false, message: "factId required for CONFIRMATION" };
    }

    const fact = this.factStore.get(event.factId);
    if (!fact) {
      return { success: false, message: `Fact ${event.factId} not found` };
    }

    const newConfidence = Math.min(0.99, fact.confidence + 0.1);
    await this.factStore.update(event.factId, { confidence: newConfidence });

    log.memory.info(
      `[MemoryFeedback] Confirmed fact ${event.factId}, confidence: ${fact.confidence} → ${newConfidence}`,
    );

    return {
      success: true,
      message: `Got it — that fact is confirmed. Confidence updated to ${newConfidence.toFixed(2)}.`,
    };
  }

  /**
   * User says a fact is wrong — retire it and optionally add corrected fact.
   */
  private async handleCorrection(
    event: FeedbackEvent,
  ): Promise<{ success: boolean; message: string }> {
    if (!event.factId) {
      return { success: false, message: "factId required for CORRECTION" };
    }

    const fact = this.factStore.get(event.factId);
    if (!fact) {
      return { success: false, message: `Fact ${event.factId} not found` };
    }

    await this.factStore.retire(event.factId);

    let message = "Got it — I've removed that incorrect fact.";

    if (event.correctedFact) {
      try {
        const added = await this.factStore.add({
          userId: fact.userId,
          fact: event.correctedFact,
          entity: fact.entity,
          category:
            (event.newCategory as typeof fact.category) ?? fact.category,
          confidence: 0.8,
          source: "inferred",
          expiresAt: fact.expiresAt,
        });
        message = `Updated the fact. New fact stored with ID ${added.id}`;
      } catch {
        message = "Fact removed, but failed to store correction.";
      }
    }

    log.memory.info(
      `[MemoryFeedback] Corrected fact ${event.factId}: ${message}`,
    );
    return { success: true, message };
  }

  /**
   * User provides contradicting information — store both + graph edge.
   */
  private async handleContradiction(
    event: FeedbackEvent,
  ): Promise<{ success: boolean; message: string }> {
    if (!event.factId || !event.correctedFact) {
      return {
        success: false,
        message: "factId and correctedFact required for CONTRADICTION",
      };
    }

    const oldFact = this.factStore.get(event.factId);
    if (!oldFact) {
      return { success: false, message: `Fact ${event.factId} not found` };
    }

    await this.factStore.add({
      userId: oldFact.userId,
      fact: event.correctedFact,
      entity: oldFact.entity,
      category:
        (event.newCategory as typeof oldFact.category) ?? oldFact.category,
      confidence: 0.85,
      source: "inferred",
      expiresAt: oldFact.expiresAt,
    });

    if (this.knowledgeGraph) {
      const nodeA = this.knowledgeGraph.addNode({
        title: oldFact.fact.slice(0, 50),
        content: oldFact.fact,
        source: "fact_store",
        domain: "user_memory",
        confidence: oldFact.confidence,
      });
      const nodeB = this.knowledgeGraph.addNode({
        title: event.correctedFact.slice(0, 50),
        content: event.correctedFact,
        source: "fact_store",
        domain: "user_memory",
        confidence: 0.85,
      });
      try {
        this.knowledgeGraph.addEdge(
          nodeA,
          nodeB,
          "contradicts",
          1.0,
          event.message,
        );
      } catch {
        log.memory.warn(
          `[MemoryFeedback] Could not add contradiction edge to graph`,
        );
      }
    }

    log.memory.info(
      `[MemoryFeedback] Contradiction stored: "${oldFact.fact.slice(0, 40)}" ↔ "${event.correctedFact.slice(0, 40)}"`,
    );

    return {
      success: true,
      message:
        "Interesting — I've noted the contradiction. Both facts are preserved for transparency.",
    };
  }

  /**
   * User provides a more precise version of a fact.
   */
  private async handleRefinement(
    event: FeedbackEvent,
  ): Promise<{ success: boolean; message: string }> {
    if (!event.factId || !event.correctedFact) {
      return {
        success: false,
        message: "factId and correctedFact required for REFINEMENT",
      };
    }

    const fact = this.factStore.get(event.factId);
    if (!fact) {
      return { success: false, message: `Fact ${event.factId} not found` };
    }

    await this.factStore.update(event.factId, {
      fact: event.correctedFact,
      confidence: Math.min(0.99, fact.confidence + 0.05),
    });

    log.memory.info(
      `[MemoryFeedback] Refined fact ${event.factId}: "${fact.fact.slice(0, 40)}" → "${event.correctedFact.slice(0, 40)}"`,
    );

    return {
      success: true,
      message: "Thanks — I've updated that fact to be more precise.",
    };
  }

  /**
   * Decay confidence of facts based on TTL / access patterns.
   * Called periodically (e.g., on startup or nightly).
   */
  async decayConfidence(): Promise<{ decayed: number; removed: number }> {
    let decayed = 0;
    let removed = 0;

    for (const fact of this.factStore.getAll()) {
      const ageMs = Date.now() - new Date(fact.updatedAt).getTime();
      const ageWeeks = ageMs / (7 * 24 * 60 * 60 * 1000);

      const decay = ageWeeks * 0.05;
      const newConfidence = Math.max(0.1, fact.confidence - decay);

      if (newConfidence < fact.confidence) {
        this.factStore.update(fact.id, { confidence: newConfidence });
        decayed++;
      }

      if (fact.confidence < 0.15 || this.isExpired(fact)) {
        this.factStore.retire(fact.id);
        removed++;
      }
    }

    log.memory.info(
      `[MemoryFeedback] Decay complete: ${decayed} confidence-adjusted, ${removed} removed`,
    );

    return { decayed, removed };
  }

  private isExpired(fact: StoredFact): boolean {
    if (!fact.expiresAt) return false;
    return new Date(fact.expiresAt).getTime() < Date.now();
  }
}
