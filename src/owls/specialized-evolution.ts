/**
 * StackOwl — Specialized Owl Evolution
 *
 * Extends the base evolution engine to handle specialized owls
 * stored in the database with their DNA.
 */

import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

export class SpecializedOwlEvolution {
  private db: MemoryDatabase;

  constructor(db: MemoryDatabase) {
    this.db = db;
  }

  /**
   * Update owl DNA based on interaction feedback.
   * Called after a successful or failed interaction.
   */
  updateOwlDNA(
    owlName: string,
    ownerId: string,
    outcome: "success" | "failure" | "partial",
  ): void {
    const owl = this.db.owls.getByName(ownerId, owlName);
    if (!owl) return;

    const dna = owl.dna;

    if (outcome === "success") {
      dna.routingQuality = Math.min(1.0, dna.routingQuality + 0.05);
    } else if (outcome === "failure") {
      dna.routingQuality = Math.max(0.1, dna.routingQuality - 0.03);
    }

    this.db.owls.update(owl.id, { dna });

    log.evolution.info(
      `[SpecializedOwlEvolution] Updated DNA for ${owlName}: routingQuality=${dna.routingQuality.toFixed(2)}`,
    );
  }

  /**
   * Record a routing decision and its outcome for evolution feedback.
   */
  recordRoutingOutcome(
    ownerId: string,
    _message: string,
    routedTo: string | null,
    outcome: "success" | "failure" | "partial",
  ): void {
    if (routedTo) {
      this.updateOwlDNA(routedTo, ownerId, outcome);
    }
  }

  /**
   * Get evolution stats for an owl.
   */
  getEvolutionStats(ownerId: string, owlName: string): OwlEvolutionStats | null {
    const owl = this.db.owls.getByName(ownerId, owlName);
    if (!owl) return null;

    return {
      name: owl.name,
      routingQuality: owl.dna.routingQuality,
      expertiseDomains: owl.dna.expertiseDomains,
      evolutionSpeed: owl.dna.evolutionSpeed,
      challengeLevel: owl.dna.challengeLevel,
      createdAt: owl.createdAt,
      updatedAt: owl.updatedAt,
    };
  }

  /**
   * List all owls with their evolution stats.
   */
  listOwlsWithStats(ownerId: string): OwlEvolutionStats[] {
    const owls = this.db.owls.getByOwner(ownerId);
    return owls.map((owl) => ({
      name: owl.name,
      routingQuality: owl.dna.routingQuality,
      expertiseDomains: owl.dna.expertiseDomains,
      evolutionSpeed: owl.dna.evolutionSpeed,
      challengeLevel: owl.dna.challengeLevel,
      createdAt: owl.createdAt,
      updatedAt: owl.updatedAt,
    }));
  }
}

export interface OwlEvolutionStats {
  name: string;
  routingQuality: number;
  expertiseDomains: string[];
  evolutionSpeed: number;
  challengeLevel: number;
  createdAt: string;
  updatedAt: string;
}