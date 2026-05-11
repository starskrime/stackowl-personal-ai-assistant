import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../../logger.js";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

const DEFAULT_MEMORY_PATH = join(homedir(), ".stackowl", "workspace", "MEMORY.md");

export class MemoryMdLayer implements ContextLayer {
  name = "MemoryMdLayer";
  priority = 0;
  maxTokens = 800;
  produces = ["tier0_memory"];
  dependsOn: string[] = [];

  constructor(private readonly memoryPath: string = DEFAULT_MEMORY_PATH) {}

  getCacheKey(): string | null {
    return null; // Never cached — reads fresh every turn
  }

  shouldFire(_triage: TriageSignals): boolean {
    return true; // Always fires — Tier 0 is unconditional
  }

  async build(
    _req: ContextRequest,
    _triage: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    if (!existsSync(this.memoryPath)) {
      return "";
    }
    try {
      const content = readFileSync(this.memoryPath, "utf-8").trim();
      if (!content) return "";
      log.engine.debug("[MemoryMdLayer] Injecting MEMORY.md", {
        path: this.memoryPath,
        chars: content.length,
      });
      return `<tier0_memory>\n${content}\n</tier0_memory>`;
    } catch (err) {
      log.engine.error("[MemoryMdLayer] Failed to read MEMORY.md", err as Error, {
        path: this.memoryPath,
      });
      return "";
    }
  }
}
