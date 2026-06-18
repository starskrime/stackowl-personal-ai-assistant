import { log } from "../logger.js";
import type { MemoryManager } from "../memory/memory-manager.js";
import type { Fact } from "../memory/fact-schema.js";

export class UnifiedMemoryRetriever {
  constructor(private memoryManager?: MemoryManager) {}

  async retrieve(query: string, _userId: string): Promise<string> {
    if (!this.memoryManager) {
      log.engine.debug("[UnifiedMemoryRetriever] no MemoryManager wired — skip");
      return "";
    }

    log.engine.debug("[UnifiedMemoryRetriever] retrieve: entry", { query: query.slice(0, 80) });

    const facts = await this.memoryManager.search(query);
    if (facts.length === 0) return "";

    const formatted = facts
      .map((f: Fact) => `[${f.type}] ${f.content}`)
      .join("\n");

    log.engine.debug("[UnifiedMemoryRetriever] retrieve: exit", { factCount: facts.length });
    return `<unified_memory>\n${formatted}\n</unified_memory>`;
  }
}
