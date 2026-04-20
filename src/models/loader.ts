/**
 * StackOwl — Model Definition Loader
 *
 * Scans src/models/ for provider definition files.
 * Each file is named after the provider (e.g. "anthropic", "openai")
 * and uses a simple key:value format.
 *
 * Example file (src/models/anthropic):
 *   compatible: anthropic
 *   availableModels: ["claude-sonnet-4-6"]
 *   defaultModel: "claude-sonnet-4-6"
 *   url: "https://api.anthropic.com/v1"
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export type ProtocolId = "openai" | "anthropic" | "gemini" | "grok";

export interface ModelDefinition {
  /** File name — used as the lookup key */
  name: string;
  /** Which of the 4 protocol implementations to use */
  compatible: ProtocolId;
  /** Models supported by this provider */
  availableModels: string[];
  /** Default model when activeModel is not set in config */
  defaultModel: string;
  /** Base URL for the provider API */
  url: string;
  /** Whether an API key is required. Default: true */
  requiresApiKey?: boolean;
}

// ─── Parser ─────────────────────────────────────────────────────

function parseModelFile(name: string, content: string): ModelDefinition | null {
  const result: Record<string, unknown> = { name };

  for (const rawLine of content.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;

    const colonIdx = line.indexOf(":");
    if (colonIdx < 0) continue;

    const key = line.slice(0, colonIdx).trim();
    const rawVal = line.slice(colonIdx + 1).trim();
    if (!key || !rawVal) continue;

    try {
      result[key] = JSON.parse(rawVal);
    } catch {
      result[key] = rawVal; // plain string (e.g. compatible: anthropic)
    }
  }

  if (!result["compatible"] || !result["url"]) return null;

  // Ensure array types
  if (!result["availableModels"]) {
    result["availableModels"] = [];
  }
  if (!result["defaultModel"]) {
    const models = result["availableModels"] as string[];
    result["defaultModel"] = models[0] ?? "";
  }

  return result as unknown as ModelDefinition;
}

// ─── Loader ─────────────────────────────────────────────────────

export class ModelLoader {
  private defs = new Map<string, ModelDefinition>();

  constructor(modelsDir?: string) {
    const dir =
      modelsDir ??
      join(dirname(fileURLToPath(import.meta.url)));
    this._loadDir(dir);
  }

  private _loadDir(dir: string): void {
    try {
      const entries = readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        // Skip compiled JS/TS files and source maps
        if (/\.(ts|js|map|json)$/.test(entry.name)) continue;

        try {
          const content = readFileSync(join(dir, entry.name), "utf-8");
          const def = parseModelFile(entry.name, content);
          if (def) this.defs.set(entry.name, def);
        } catch {
          // Skip unreadable files
        }
      }
    } catch {
      // Directory may not exist in test environments
    }
  }

  get(name: string): ModelDefinition | null {
    return this.defs.get(name) ?? null;
  }

  getAll(): ModelDefinition[] {
    return Array.from(this.defs.values());
  }

  has(name: string): boolean {
    return this.defs.has(name);
  }
}

/** Singleton instance — shared across the process */
let _loaderInstance: ModelLoader | null = null;

export function getModelLoader(): ModelLoader {
  if (!_loaderInstance) {
    _loaderInstance = new ModelLoader();
  }
  return _loaderInstance;
}
