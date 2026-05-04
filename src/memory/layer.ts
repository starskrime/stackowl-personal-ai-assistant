/**
 * StackOwl — Element 15 — Memory ContextLayer factories.
 *
 * Four read-side surfaces over MemoryRepository, one per renderable kind
 * (semantic / episodic / working / procedural). Reflexive memories are
 * excluded by construction — engine self-observations don't belong in the
 * prompt. Each factory returns a `ContextLayer` that the existing
 * `ContextPipeline` runs through DAG batching, budget control, and caching.
 *
 * Scoring is delegated to `MemoryRepository.search()` (recency × importance ×
 * relevance). The layer's only job is filter-by-kind, format, and truncate to
 * the per-layer token budget.
 */

import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../context/layer.js";
import type { MemoryRepository, MemoryKind } from "./repository.js";

export interface MemoryLayerDeps {
  repo: MemoryRepository;
}

interface MemoryLayerConfig {
  name: string;
  priority: number;
  maxTokens: number;
  cacheTtlMs: number;
  kind: MemoryKind;
  topK: number;
  minImportance?: number;
  header: string;
}

const CHARS_PER_TOKEN = 4;

function makeMemoryLayer(deps: MemoryLayerDeps, cfg: MemoryLayerConfig): ContextLayer {
  return {
    name: cfg.name,
    priority: cfg.priority,
    maxTokens: cfg.maxTokens,
    produces: [cfg.name],
    dependsOn: [],
    cacheTtlMs: cfg.cacheTtlMs,

    shouldFire(_triage: TriageSignals): boolean {
      return true;
    },

    async build(req: ContextRequest, triage: TriageSignals, _deps: LayerResults): Promise<string> {
      const query =
        (req as unknown as { userMessage?: string }).userMessage ??
        triage?.userMessage ??
        "";
      const records = await deps.repo.search(query, {
        kinds: [cfg.kind],
        topK: cfg.topK,
        minImportance: cfg.minImportance,
      });
      if (records.length === 0) return "";

      const sorted = [...records].sort((a, b) => b.importance - a.importance);
      const lines = sorted.map((r) => `- ${r.content}`);
      const result = `${cfg.header}\n${lines.join("\n")}`;
      const maxChars = cfg.maxTokens * CHARS_PER_TOKEN;
      return result.length > maxChars ? result.slice(0, maxChars - 3) + "..." : result;
    },

    getCacheKey(req: ContextRequest, _triage: TriageSignals): string {
      const sessionId =
        (req as unknown as { sessionId?: string }).sessionId ??
        req.session?.id ??
        "global";
      return `${cfg.name}:${sessionId}`;
    },
  };
}

export function createSemanticMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.semantic",
    priority: 7,
    maxTokens: 800,
    cacheTtlMs: 5 * 60_000,
    kind: "semantic",
    topK: 6,
    header: "## Long-term facts about the user",
  });
}

export function createEpisodicMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.episodic",
    priority: 6,
    maxTokens: 600,
    cacheTtlMs: 2 * 60_000,
    kind: "episodic",
    topK: 5,
    header: "## Recent events",
  });
}

export function createWorkingMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.working",
    priority: 8,
    maxTokens: 400,
    cacheTtlMs: 30_000,
    kind: "working",
    topK: 4,
    header: "## Active task state",
  });
}

export function createProceduralMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.procedural",
    priority: 5,
    maxTokens: 600,
    cacheTtlMs: 10 * 60_000,
    kind: "procedural",
    topK: 4,
    header: "## Learned procedures",
  });
}

export function createMemoryLayers(deps: MemoryLayerDeps): ContextLayer[] {
  return [
    createSemanticMemoryLayer(deps),
    createEpisodicMemoryLayer(deps),
    createWorkingMemoryLayer(deps),
    createProceduralMemoryLayer(deps),
  ];
}
