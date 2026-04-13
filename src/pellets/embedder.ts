/**
 * StackOwl — Pellet Embedder
 *
 * Thin wrapper around the configured provider's embed() call.
 * Initialized once at startup; used by LancePelletStore and KuzuPelletGraph.
 *
 * - Uses Ollama nomic-embed-text by default (768-dim, local, fast)
 * - In-process LRU cache (1 000 entries) avoids re-embedding identical text
 * - Graceful degradation: returns null when provider is unavailable
 */

import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

// ─── State ───────────────────────────────────────────────────────

let _provider: ModelProvider | null = null;
let _dim: number | null = null;
const _cache = new Map<string, number[]>();
const MAX_CACHE = 1_000;

// ─── Init ────────────────────────────────────────────────────────

export function initEmbedder(provider: ModelProvider): void {
  _provider = provider;
  _cache.clear();
  log.engine.info("[Embedder] Initialized (Ollama nomic-embed-text)");
}

export function isEmbedderReady(): boolean {
  return _provider !== null;
}

export function getEmbeddingDim(): number {
  return _dim ?? 768; // nomic-embed-text default
}

// ─── Embed ───────────────────────────────────────────────────────

/**
 * Embed text into a float vector.
 * Returns null if the provider is unavailable or embedding fails.
 */
export async function embed(text: string): Promise<number[] | null> {
  if (!_provider) return null;

  const normalized = text.trim().slice(0, 2000);
  if (!normalized) return null;

  // Cache key: first 300 chars (enough for uniqueness, cheap to hash)
  const key = normalized.slice(0, 300);
  if (_cache.has(key)) return _cache.get(key)!;

  try {
    const res = await _provider.embed(normalized);
    const vec = res?.embedding;
    if (!vec || vec.length === 0) return null;

    // Record dimension on first successful embed
    if (_dim === null) {
      _dim = vec.length;
      log.engine.info(`[Embedder] Detected embedding dim: ${_dim}`);
    }

    // Evict oldest entries when cache is full
    if (_cache.size >= MAX_CACHE) {
      const firstKey = _cache.keys().next().value;
      if (firstKey !== undefined) _cache.delete(firstKey);
    }

    _cache.set(key, vec);
    return vec;
  } catch (err) {
    log.engine.warn(
      `[Embedder] embed() failed: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

// ─── Pellet text builder ─────────────────────────────────────────

/**
 * Produce the canonical text to embed for a pellet.
 * Layout: title → tags → first 1 500 chars of content.
 * Keeps token budget reasonable while capturing all semantic dimensions.
 */
export function pelletToEmbedText(p: {
  title: string;
  tags: string[];
  content: string;
}): string {
  return [`title: ${p.title}`, `tags: ${p.tags.join(" ")}`, p.content.slice(0, 1500)].join(
    "\n",
  );
}
