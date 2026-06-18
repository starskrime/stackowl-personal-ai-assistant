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
import { log } from "../logger.js";

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

const VALID_PROTOCOLS = new Set<string>(["openai", "anthropic", "gemini", "grok"]);

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

  // Validate protocol — reject unknown ProtocolId values so downstream
  // switch/exhaustive checks never encounter an invalid protocol silently
  if (!VALID_PROTOCOLS.has(result["compatible"] as string)) {
    // Caller's warn log will surface this via the unreadable-file path
    return null;
  }

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
  private systemNames = new Set<string>();

  constructor(extraDirs?: string[]) {
    const systemDir = dirname(fileURLToPath(import.meta.url));
    this._loadDir(systemDir);
    // Record which names came from the system directory
    for (const name of this.defs.keys()) {
      this.systemNames.add(name);
    }
    if (extraDirs) {
      for (const dir of extraDirs) {
        this._loadDir(dir, /* skipSystemNames */ true);
      }
    }
  }

  private _loadDir(dir: string, skipSystemNames = false): void {
    log.engine.debug("model-loader._loadDir: entry", { dir, skipSystemNames });
    let loaded = 0;
    let skipped = 0;
    try {
      const entries = readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        // Skip compiled JS/TS files and source maps
        if (/\.(ts|js|map|json)$/.test(entry.name)) continue;
        // Protect system names from user-directory overrides
        if (skipSystemNames && this.systemNames.has(entry.name)) {
          log.engine.debug("model-loader._loadDir: skipped reserved name", { name: entry.name });
          skipped++;
          continue;
        }

        try {
          const content = readFileSync(join(dir, entry.name), "utf-8");
          const def = parseModelFile(entry.name, content);
          if (def) {
            this.defs.set(entry.name, def);
            loaded++;
          } else {
            log.engine.warn("model-loader._loadDir: malformed model file skipped", new Error("parse returned null"), { file: entry.name, dir });
          }
        } catch (err) {
          log.engine.warn("model-loader._loadDir: unreadable file skipped", err as Error, { file: entry.name, dir });
        }
      }
    } catch {
      // Directory may not exist or is inaccessible — expected for user dirs
      log.engine.debug("model-loader._loadDir: directory not accessible", { dir });
    }
    log.engine.debug("model-loader._loadDir: exit", { dir, loaded, skipped });
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

  isSystemName(name: string): boolean {
    return this.systemNames.has(name);
  }
}

// ─── Singleton ───────────────────────────────────────────────────

/** Singleton instance — shared across the process */
let _loaderInstance: ModelLoader | null = null;

export function initModelLoader(workspaceModelsDir?: string): ModelLoader {
  if (_loaderInstance) {
    log.engine.warn("model-loader.initModelLoader: re-initializing existing singleton", new Error("re-init"), { workspaceModelsDir });
  }
  _loaderInstance = new ModelLoader(
    workspaceModelsDir ? [workspaceModelsDir] : undefined,
  );
  return _loaderInstance;
}

export function getModelLoader(): ModelLoader {
  if (!_loaderInstance) {
    _loaderInstance = new ModelLoader();
  }
  return _loaderInstance;
}

export function resetModelLoader(): void {
  _loaderInstance = null;
}
