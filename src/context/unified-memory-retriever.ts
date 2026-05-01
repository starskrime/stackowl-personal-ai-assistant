import type { MemoryBus, UnifiedMemory } from "../memory/bus.js";
import type { FactStore } from "../memory/fact-store.js";
import type { EpisodicMemory, Episode } from "../memory/episodic.js";

export class UnifiedMemoryRetriever {
  constructor(
    private memoryBus: MemoryBus,
    private factStore: FactStore,
    private episodic: EpisodicMemory,
  ) {}

  async retrieve(query: string, userId: string): Promise<string> {
    const [busResults, facts, episodes] = await Promise.all([
      this.memoryBus.recall(query, 10, 2000).catch(() => [] as UnifiedMemory[]),
      Promise.resolve(this.factStore.search(query, userId, 10)).catch(() => []),
      this.episodic.search(query, 5, undefined).catch(() => [] as Episode[]),
    ]);

    if (facts.length === 0 && episodes.length === 0 && busResults.length === 0) return "";

    // Collect all content for dedup
    const seen = new Map<string, { content: string; relevance: number; tier: string }>();

    for (const f of facts) {
      const key = normalize(f.fact);
      if (!seen.has(key)) seen.set(key, { content: f.fact, relevance: f.confidence ?? 0.7, tier: "long_term" });
    }
    for (const e of episodes) {
      const key = normalize(e.summary);
      const existing = seen.get(key);
      const importance = e.importance ?? 0;
      if (!existing) {
        seen.set(key, { content: e.summary, relevance: importance, tier: "episodic" });
      } else if (existing.tier !== "long_term" && importance > existing.relevance) {
        seen.set(key, { content: e.summary, relevance: importance, tier: "episodic" });
      }
    }
    for (const b of busResults) {
      const key = normalize(b.content);
      const existing = [...seen.values()].find((v) => cosineSim(normalize(v.content), key) > 0.9);
      if (!existing) seen.set(key, { content: b.content, relevance: b.relevance, tier: "semantic" });
    }

    const all = [...seen.values()].sort((a, b) => b.relevance - a.relevance).slice(0, 10);
    const byTier = new Map<string, string[]>();
    for (const item of all) {
      const list = byTier.get(item.tier) ?? [];
      list.push(item.content);
      byTier.set(item.tier, list);
    }

    const lines = ["<memory>"];
    if (byTier.has("long_term")) {
      lines.push(`  <facts tier="long_term" confidence="high">`);
      for (const c of byTier.get("long_term") ?? []) lines.push(`    ${c}`);
      lines.push("  </facts>");
    }
    if (byTier.has("episodic")) {
      lines.push(`  <episodes tier="episodic" recency="recent">`);
      for (const c of byTier.get("episodic") ?? []) lines.push(`    ${c}`);
      lines.push("  </episodes>");
    }
    if (byTier.has("semantic")) {
      lines.push(`  <bus tier="semantic" relevance="high">`);
      for (const c of byTier.get("semantic") ?? []) lines.push(`    ${c}`);
      lines.push("  </bus>");
    }
    lines.push("</memory>");
    return lines.join("\n");
  }
}

function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim().slice(0, 120);
}

function cosineSim(a: string, b: string): number {
  const aWords = new Set(a.split(" ").filter((w) => w.length > 3));
  const bWords = new Set(b.split(" ").filter((w) => w.length > 3));
  if (aWords.size === 0 || bWords.size === 0) return 0;
  const intersection = [...aWords].filter((w) => bWords.has(w)).length;
  return intersection / Math.sqrt(aWords.size * bWords.size);
}
