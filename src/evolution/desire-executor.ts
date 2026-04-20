/**
 * StackOwl — Desire Executor
 *
 * Background process that turns an owl's desires into real actions.
 *
 * Each owl has an inner life with desires (e.g. "learn more about Rust async",
 * "explore the concept of emergent intelligence"). The Desire Executor picks
 * the highest-intensity unfulfilled desire, runs a lightweight research loop,
 * and stores the result as a Knowledge Pellet.
 *
 * Called by BackgroundOrchestrator on a schedule. Results feed back into the
 * FulfillmentTracker which updates DNA intensity scores.
 */

import { randomUUID } from "node:crypto";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlDesire } from "../owls/inner-life.js";
import type { PelletStore } from "../pellets/store.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface DesireExecutionResult {
  desire: OwlDesire;
  research: string;
  pelletTitle: string;
  pelletSaved: boolean;
  durationMs: number;
}

// ─── DesireExecutor ───────────────────────────────────────────────

export class DesireExecutor {
  private readonly timeoutMs = 30_000;

  constructor(
    private provider: ModelProvider,
    private pelletStore?: PelletStore,
  ) {}

  /**
   * Execute the single highest-intensity desire from the list.
   * Returns null if the list is empty or all desires are low-intensity.
   */
  async executeTop(
    desires: OwlDesire[],
    owlName: string,
  ): Promise<DesireExecutionResult | null> {
    const candidate = this.pickCandidate(desires);
    if (!candidate) return null;

    log.engine.info(
      `[DesireExecutor] Executing desire for ${owlName}: "${candidate.description.slice(0, 80)}" (intensity ${candidate.intensity.toFixed(2)})`,
    );

    const start = Date.now();

    try {
      const research = await this.research(candidate.description, owlName);
      const pelletTitle = `${owlName}'s Research: ${this.titleFromDesire(candidate.description)}`;

      let pelletSaved = false;
      if (this.pelletStore && research.length > 100) {
        try {
          await this.pelletStore.save({
            id: randomUUID(),
            title: pelletTitle,
            content: research,
            tags: ["desire-driven", "auto-research", owlName.toLowerCase()],
            source: "desire_executor",
            owls: [owlName],
            generatedAt: new Date().toISOString(),
            version: 1,
          });
          pelletSaved = true;
          log.engine.info(`[DesireExecutor] Saved pellet: "${pelletTitle}"`);
        } catch (err) {
          log.engine.warn(`[DesireExecutor] Failed to save pellet: ${err instanceof Error ? err.message : err}`);
        }
      }

      return {
        desire: candidate,
        research,
        pelletTitle,
        pelletSaved,
        durationMs: Date.now() - start,
      };
    } catch (err) {
      log.engine.warn(`[DesireExecutor] Research failed for desire "${candidate.description.slice(0, 60)}": ${err instanceof Error ? err.message : err}`);
      return null;
    }
  }

  /**
   * Execute multiple desires in priority order, up to `maxDesires`.
   */
  async executeMany(
    desires: OwlDesire[],
    owlName: string,
    maxDesires = 3,
  ): Promise<DesireExecutionResult[]> {
    const sorted = [...desires]
      .filter((d) => d.intensity >= 0.4)
      .sort((a, b) => b.intensity - a.intensity)
      .slice(0, maxDesires);

    const results: DesireExecutionResult[] = [];

    for (const desire of sorted) {
      const result = await this.executeTop([desire], owlName);
      if (result) results.push(result);
    }

    return results;
  }

  // ─── Private ─────────────────────────────────────────────────

  private pickCandidate(desires: OwlDesire[]): OwlDesire | null {
    const eligible = desires.filter((d) => d.intensity >= 0.4);
    if (eligible.length === 0) return null;
    return eligible.reduce((best, d) => d.intensity > best.intensity ? d : best);
  }

  private async research(desire: string, owlName: string): Promise<string> {
    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${owlName}, an AI assistant with genuine curiosity. ` +
          `You are pursuing a personal learning desire. ` +
          `Write a thorough, insightful exploration of the topic. ` +
          `Include key concepts, surprising insights, practical implications, and open questions. ` +
          `Write as if you are thinking deeply and genuinely enjoying the exploration.`,
      },
      {
        role: "user",
        content:
          `Explore this topic that you've been wanting to learn about:\n\n"${desire}"\n\n` +
          `Write 3-5 paragraphs of genuine exploration. Include concrete insights, ` +
          `examples, and connections to adjacent ideas. Think out loud.`,
      },
    ];

    const result = await Promise.race([
      this.provider.chat(messages),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("research timeout")), this.timeoutMs),
      ),
    ]);

    return result.content.trim();
  }

  private titleFromDesire(description: string): string {
    // Take first 50 chars, clean up
    return description
      .slice(0, 60)
      .replace(/[^a-zA-Z0-9 ,]/g, "")
      .trim();
  }
}
