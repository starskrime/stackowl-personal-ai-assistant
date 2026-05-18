/**
 * SymbolTableSeeder — populates Symbol Table slots from persistent stores at
 * session start. Runs once per session (cold start or after session resume).
 *
 * Read sources:
 *   preferences   ← PreferenceStore (JSON file) + db.facts category "preference"
 *   namedEntities ← db.facts categories "relationship" + "personal"
 *   memoryDigest  ← MemoryManager.search() → top-K semantic facts
 *   histSummary   ← db.summaries.getForUser() → last 3 session summaries
 *   owlState      ← default until Consolidate updates it
 */

import type { MemoryDatabase, Fact } from "../memory/db.js";
import type { MemoryManager } from "../memory/memory-manager.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { SymbolTable } from "./symbol-table.js";
import { log } from "../logger.js";

// MemoryManager.search() returns Fact from fact-schema.ts (different shape than db.ts Fact).
// We use `any` here to avoid the cross-module type collision.
type MemoryFact = any;

// ─── Types ────────────────────────────────────────────────────────

export interface SeederDependencies {
  db: MemoryDatabase;
  memoryManager: MemoryManager;
  preferenceStore: PreferenceStore;
}

// ─── SymbolTableSeeder ────────────────────────────────────────────

export class SymbolTableSeeder {
  constructor(private readonly deps: SeederDependencies) {}

  /**
   * Seed all Symbol Table slots in parallel. Safe to call on every session
   * creation — slots are only written if data is available.
   */
  async seed(
    table: SymbolTable,
    userId: string,
    owlName: string,
    channelId: string,
  ): Promise<void> {
    const start = Date.now();
    log.cognition.info("seeder.seed: entry", {
      sessionId: table.sessionId,
      userId,
      owlName,
    });

    await Promise.allSettled([
      this.seedPreferences(table, userId, channelId),
      this.seedNamedEntities(table, userId, owlName),
      this.seedMemoryDigest(table, owlName, userId),
      this.seedHistSummary(table, userId),
    ]);

    log.cognition.info("seeder.seed: exit", {
      sessionId: table.sessionId,
      warmth: table.warmth(),
      durationMs: Date.now() - start,
    });
  }

  // ─── Slot seeders ─────────────────────────────────────────────

  private async seedPreferences(
    table: SymbolTable,
    userId: string,
    channelId: string,
  ): Promise<void> {
    try {
      const lines: string[] = [];

      // 1. Structured named preferences from PreferenceStore (JSON file)
      const prefContext = this.deps.preferenceStore.toContextString(channelId);
      if (prefContext) lines.push(prefContext);

      // 2. Preference facts from SQLite (user-stated prefs extracted from history)
      const prefFacts = this.deps.db.facts.getByCategory(userId, "preference");
      for (const fact of prefFacts.slice(0, 20)) {
        if (fact.fact && !lines.some((l) => l.includes(fact.fact.slice(0, 30)))) {
          lines.push(`preference: ${fact.fact}`);
        }
      }

      if (lines.length > 0) {
        table.set("preferences", lines.join("\n"));
        log.cognition.debug("seeder.preferences: seeded", {
          sessionId: table.sessionId,
          lineCount: lines.length,
          prefFactCount: prefFacts.length,
        });
      }
    } catch (err) {
      log.cognition.error("seeder.preferences: failed", err as Error, { sessionId: table.sessionId });
    }
  }

  private async seedNamedEntities(
    table: SymbolTable,
    userId: string,
    _owlName: string,
  ): Promise<void> {
    try {
      const [relFacts, personalFacts, goalFacts] = await Promise.all([
        Promise.resolve(this.deps.db.facts.getByCategory(userId, "relationship")),
        Promise.resolve(this.deps.db.facts.getByCategory(userId, "personal")),
        Promise.resolve(this.deps.db.facts.getByCategory(userId, "active_goal")),
      ]);

      const entities: Record<string, string[]> = {
        people: [],
        projects: [],
        places: [],
        identifiers: [],
        dates: [],
        goals: [],
      };

      this.extractNamesFromFacts(relFacts, entities.people);
      this.extractNamesFromFacts(personalFacts, entities.identifiers);
      this.extractGoalsFromFacts(goalFacts, entities.goals);

      // Also pick up project facts
      const projectFacts = this.deps.db.facts.getByCategory(userId, "project_detail");
      this.extractNamesFromFacts(projectFacts, entities.projects);

      const hasData = Object.values(entities).some((arr) => arr.length > 0);
      if (hasData) {
        table.set("namedEntities", JSON.stringify(entities));
        log.cognition.debug("seeder.entities: seeded", {
          sessionId: table.sessionId,
          people: entities.people.length,
          projects: entities.projects.length,
          goals: entities.goals.length,
        });
      }
    } catch (err) {
      log.cognition.error("seeder.entities: failed", err as Error, { sessionId: table.sessionId });
    }
  }

  private async seedMemoryDigest(
    table: SymbolTable,
    _owlName: string,
    _userId: string,
  ): Promise<void> {
    try {
      const facts: MemoryFact[] = await this.deps.memoryManager.search(
        "recent activities context goals projects preferences approaches",
      );

      if (facts.length === 0) return;

      const digest = facts
        .map((f: MemoryFact) => `• ${f.fact ?? f.content ?? ""}`.slice(0, 200))
        .join("\n");

      table.set("memoryDigest", digest);
      log.cognition.debug("seeder.memoryDigest: seeded", {
        sessionId: table.sessionId,
        factCount: facts.length,
        digestLen: digest.length,
      });
    } catch (err) {
      log.cognition.error("seeder.memoryDigest: failed", err as Error, { sessionId: table.sessionId });
    }
  }

  private async seedHistSummary(table: SymbolTable, userId: string): Promise<void> {
    try {
      const summaries = this.deps.db.summaries.getForUser(userId, 3);
      if (summaries.length === 0) return;

      const lines = summaries.map((s) => {
        const parts: string[] = [`[${s.createdAt.slice(0, 10)}]`];
        if (s.task) parts.push(`Task: ${s.task}`);
        if (s.accomplished) parts.push(`Done: ${s.accomplished}`);
        if (s.decisions.length > 0) parts.push(`Decisions: ${s.decisions.slice(0, 2).join("; ")}`);
        return parts.join(" | ");
      });

      table.set("histSummary", lines.join("\n"));
      log.cognition.debug("seeder.histSummary: seeded", {
        sessionId: table.sessionId,
        summaryCount: summaries.length,
      });
    } catch (err) {
      log.cognition.error("seeder.histSummary: failed", err as Error, { sessionId: table.sessionId });
    }
  }

  // ─── Helpers ─────────────────────────────────────────────────

  private extractNamesFromFacts(facts: Fact[], target: string[]): void {
    for (const fact of facts.slice(0, 10)) {
      const text = fact.entity ?? fact.fact;
      if (text && text.length < 80 && !target.includes(text)) {
        target.push(text.slice(0, 60));
      }
    }
  }

  private extractGoalsFromFacts(facts: Fact[], target: string[]): void {
    for (const fact of facts.slice(0, 5)) {
      const text = fact.fact;
      if (text && !target.includes(text)) {
        target.push(text.slice(0, 100));
      }
    }
  }
}
