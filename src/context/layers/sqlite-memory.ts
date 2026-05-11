import { log } from "../../logger.js";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { MemoryDatabase, Fact, FactCategory } from "../../memory/db.js";

export class SqliteTier0Layer implements ContextLayer {
  name = "SqliteTier0Layer";
  priority = 0;
  maxTokens = 800;
  produces = ["tier0_memory"];
  dependsOn: string[] = [];

  constructor(private readonly db?: MemoryDatabase) {}

  getCacheKey(): string | null {
    return null;
  }

  shouldFire(_triage: TriageSignals): boolean {
    return true;
  }

  async build(
    _req: ContextRequest,
    _triage: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    if (!this.db) return "";

    let facts: Fact[];
    try {
      facts = this.db.facts.getHighConfidenceFacts();
    } catch (err) {
      log.engine.error("[SqliteTier0Layer] Failed to query facts", err as Error);
      return "";
    }

    if (facts.length === 0) return "";

    // Group facts by category for readability
    const byCategory = new Map<FactCategory, string[]>();
    for (const f of facts) {
      const list = byCategory.get(f.category) ?? [];
      list.push(`- ${f.fact}`);
      byCategory.set(f.category, list);
    }

    const lines: string[] = [];
    for (const [category, items] of byCategory) {
      lines.push(`${category}:`);
      lines.push(...items);
    }

    log.engine.debug("[SqliteTier0Layer] Injecting tier-0 facts", {
      count: facts.length,
      categories: [...byCategory.keys()],
    });

    return `<tier0_memory>\n${lines.join("\n")}\n</tier0_memory>`;
  }
}
