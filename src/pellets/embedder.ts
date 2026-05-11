/**
 * StackOwl — Pellet Embedder
 *
 * Runs embeddings fully in-process using fastembed + ONNX runtime.
 * No external server (no Ollama), no API key, no extra cost.
 *
 * Model: BAAI/bge-small-en-v1.5 (~50 MB, 384-dim, downloads once on first use)
 * Cache: LRU 1 000 entries — avoids re-embedding identical text
 * Graceful degradation: returns null on any failure so the rest of the
 * system keeps working (search just returns empty results).
 */

import { log } from "../logger.js";

// ─── State ───────────────────────────────────────────────────────

let _embedder: import("fastembed").FlagEmbedding | null = null;
let _dim: number | null = null;
let _initPromise: Promise<void> | null = null;
let _cacheDir: string | undefined;

const _cache = new Map<string, number[]>();
const MAX_CACHE = 1_000;

// ─── Init ────────────────────────────────────────────────────────

/** Set the directory where fastembed will store its model files. Must be called before initEmbedder(). */
export function setEmbedderCacheDir(dir: string): void {
  _cacheDir = dir;
}

/**
 * Initialize the in-process embedder.
 * Downloads the model on first call (~50 MB, cached in local_cache inside workspace/memory).
 * Safe to call multiple times — only initializes once.
 */
export async function initEmbedder(): Promise<void> {
  if (_embedder) return;
  if (_initPromise) return _initPromise;

  _initPromise = (async () => {
    try {
      const { FlagEmbedding, EmbeddingModel } = await import("fastembed");
      log.engine.info("[Embedder] Loading in-process embedding model (first run may download ~50 MB)...");
      _embedder = await FlagEmbedding.init({
        model: EmbeddingModel.BGESmallENV15,
        ...(_cacheDir ? { cacheDir: _cacheDir } : {}),
      });
      // Probe dimension with a dummy embed
      const probe = await _embedder.queryEmbed("probe");
      _dim = probe.length;
      log.engine.info(`[Embedder] Ready — model: BGE-small-en-v1.5, dim: ${_dim}`);
    } catch (err) {
      log.engine.warn(`[Embedder] Failed to initialize: ${err instanceof Error ? err.message : err}`);
      _embedder = null;
    }
  })();

  return _initPromise;
}

export function isEmbedderReady(): boolean {
  return _embedder !== null;
}

export function getEmbeddingDim(): number {
  return _dim ?? 384; // BGE-small-en-v1.5 default
}

// ─── Embed ───────────────────────────────────────────────────────

/**
 * Embed text into a float vector.
 * Returns null if the embedder is unavailable or embedding fails.
 */
export async function embed(text: string): Promise<number[] | null> {
  if (!_embedder) return null;

  const normalized = text.trim().slice(0, 2000);
  if (!normalized) return null;

  const key = normalized.slice(0, 300);
  if (_cache.has(key)) return _cache.get(key)!;

  try {
    const raw = await _embedder.queryEmbed(normalized);
    if (!raw || raw.length === 0) return null;

    // fastembed returns Float32Array — convert to plain number[] so LanceDB's
    // schema inference doesn't treat it as a struct (Array.isArray fails on TypedArrays)
    const vec: number[] = Array.isArray(raw) ? (raw as number[]) : Array.from(raw as ArrayLike<number>);

    if (_cache.size >= MAX_CACHE) {
      const firstKey = _cache.keys().next().value;
      if (firstKey !== undefined) _cache.delete(firstKey);
    }

    _cache.set(key, vec);
    return vec;
  } catch (err) {
    log.engine.warn(`[Embedder] embed() failed: ${err instanceof Error ? err.message : String(err)}`);
    return null;
  }
}

// ─── Pellet text builder ─────────────────────────────────────────

/**
 * Produce the canonical text to embed for a pellet.
 * Layout: title → tags → first 1 500 chars of content.
 */
export function pelletToEmbedText(p: {
  title: string;
  tags: string[];
  content: string;
}): string {
  return [
    `title: ${p.title}`,
    `tags: ${p.tags.join(" ")}`,
    p.content.slice(0, 1500),
  ].join("\n");
}
