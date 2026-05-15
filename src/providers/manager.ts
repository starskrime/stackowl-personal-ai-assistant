/**
 * StackOwl — Provider Manager
 *
 * Orchestrating service for provider CRUD operations.
 * Coordinates ProviderRegistry, ModelLoader, and config persistence
 * so callers (CLI, Telegram menu, TUI) have a single, consistent interface.
 */

import { writeFile, unlink, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { log } from "../logger.js";
import {
  getModelLoader,
  initModelLoader,
  resetModelLoader,
} from "../models/loader.js";
import type { ProtocolId } from "../models/loader.js";
import type { ProviderRegistry } from "./registry.js";
import type { StackOwlConfig, ProviderConfigEntry } from "../config/loader.js";

// ─── Public Types ────────────────────────────────────────────────

export interface CustomProviderConfig {
  compatible: ProtocolId;
  url: string;
  availableModels: string[];
  defaultModel: string;
}

export interface AddProviderInput {
  name: string;
  /** Model file / protocol key (e.g. "openai", "anthropic", or a custom name) */
  profile?: string;
  apiKey?: string;
  activeModel?: string;
  baseUrl?: string;
  customConfig?: CustomProviderConfig;
}

export interface ProviderUpdates {
  apiKey?: string;
  activeModel?: string;
  baseUrl?: string;
}

export interface ProviderStatus {
  name: string;
  /** Resolved profile / model-file key */
  profile: string;
  activeModel: string;
  isDefault: boolean;
  health: "CLOSED" | "OPEN" | "HALF_OPEN" | "unconfigured";
  /** "system" = built-in model file; "custom" = user-defined workspace model file */
  source: "system" | "custom";
}

export interface TestResult {
  ok: boolean;
  latencyMs: number;
  error?: string;
}

// ─── Validation ──────────────────────────────────────────────────

/**
 * Valid provider name: lowercase alphanumeric, hyphens allowed in the middle.
 * Single char (a-z or 0-9) is also allowed.
 */
const NAME_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$|^[a-z0-9]$/;

// ─── Manager ────────────────────────────────────────────────────

export class ProviderManager {
  constructor(
    private registry: ProviderRegistry,
    private config: StackOwlConfig,
    private workspacePath: string,
    private saveFn: (config: StackOwlConfig) => Promise<void>,
  ) {}

  /** Absolute path to the user-managed model definitions directory */
  get workspaceModelsDir(): string {
    return join(this.workspacePath, "models");
  }

  // ── addProvider ────────────────────────────────────────────────

  async addProvider(input: AddProviderInput): Promise<void> {
    log.engine.debug("provider-manager.addProvider: entry", { name: input.name });

    // 1. Validate name format
    if (!NAME_RE.test(input.name)) {
      throw new Error(
        `Invalid provider name "${input.name}". Use lowercase alphanumeric characters and hyphens only (e.g. "my-openai").`,
      );
    }

    // 2. Guard against reserved system names
    if (getModelLoader().isSystemName(input.name)) {
      log.engine.debug("provider-manager.addProvider: decision — reserved system name", {
        name: input.name,
      });
      throw new Error(
        `Name "${input.name}" is reserved by a built-in model definition. Choose a different name (e.g. "${input.name}-prod").`,
      );
    }

    // 3. Guard against duplicates in config
    if (this.config.providers[input.name]) {
      log.engine.debug("provider-manager.addProvider: decision — duplicate name", {
        name: input.name,
      });
      throw new Error(`Provider "${input.name}" already exists in config.`);
    }

    // 4. Write custom model definition file if provided
    if (input.customConfig) {
      log.engine.debug("provider-manager.addProvider: step — writing custom model file", {
        name: input.name,
      });
      await mkdir(this.workspaceModelsDir, { recursive: true });
      await this._writeModelFile(input.name, input.customConfig);
      resetModelLoader();
      initModelLoader(this.workspaceModelsDir);
      log.engine.debug("provider-manager.addProvider: step — model loader re-initialised", {
        workspaceModelsDir: this.workspaceModelsDir,
      });
    }

    // 5. Write config entry and persist
    const entry: ProviderConfigEntry = {
      ...(input.profile !== undefined && { profile: input.profile }),
      ...(input.apiKey !== undefined && { apiKey: input.apiKey }),
      ...(input.activeModel !== undefined && { activeModel: input.activeModel }),
      ...(input.baseUrl !== undefined && { baseUrl: input.baseUrl }),
    };
    this.config.providers[input.name] = entry;
    log.engine.debug("provider-manager.addProvider: step — config entry written", {
      name: input.name,
    });
    await this.saveFn(this.config);

    // 6. Hot-register in the live registry (best-effort)
    try {
      this.registry.register({ name: input.name, ...entry });
    } catch (err) {
      log.engine.warn(
        "provider-manager.addProvider: hot-register failed — provider persisted but not live until restart",
        err as Error,
        { name: input.name },
      );
    }

    log.engine.debug("provider-manager.addProvider: exit", { name: input.name });
  }

  // ── editProvider ───────────────────────────────────────────────

  async editProvider(name: string, updates: ProviderUpdates): Promise<void> {
    log.engine.debug("provider-manager.editProvider: entry", { name });

    const entry = this.config.providers[name];
    if (!entry) {
      throw new Error(`Provider "${name}" not found in config.`);
    }

    log.engine.debug("provider-manager.editProvider: decision — applying updates", {
      name,
      fields: Object.keys(updates),
    });

    if (updates.apiKey !== undefined) entry.apiKey = updates.apiKey;
    if (updates.activeModel !== undefined) entry.activeModel = updates.activeModel;
    if (updates.baseUrl !== undefined) entry.baseUrl = updates.baseUrl;

    log.engine.debug("provider-manager.editProvider: step — saving config", { name });
    await this.saveFn(this.config);

    // Re-register to pick up the new credentials in the live process
    log.engine.debug("provider-manager.editProvider: step — re-registering provider", {
      name,
    });
    this.registry.deregister(name);
    try {
      this.registry.register({ name, ...entry });
    } catch (err) {
      log.engine.warn(
        "provider-manager.editProvider: re-register failed — changes persisted, restart may be needed",
        err as Error,
        { name },
      );
    }

    log.engine.debug("provider-manager.editProvider: exit", { name });
  }

  // ── deleteProvider ─────────────────────────────────────────────

  async deleteProvider(name: string): Promise<void> {
    log.engine.debug("provider-manager.deleteProvider: entry", { name });

    if (this.config.defaultProvider === name) {
      throw new Error(
        `Cannot delete "${name}" — it is the current default provider. Set another provider as default first.`,
      );
    }

    log.engine.debug("provider-manager.deleteProvider: decision — proceeding with deletion", {
      name,
    });

    // Remove from live registry first (safe even if not registered)
    log.engine.debug("provider-manager.deleteProvider: step — deregistering from registry", {
      name,
    });
    this.registry.deregister(name);

    // Remove from config and persist
    delete this.config.providers[name];
    log.engine.debug("provider-manager.deleteProvider: step — saving config", { name });
    await this.saveFn(this.config);

    // Clean up custom model definition file if present (silent if missing)
    try {
      await unlink(join(this.workspaceModelsDir, name));
      log.engine.debug("provider-manager.deleteProvider: step — removed custom model file", {
        name,
      });
    } catch {
      // Not a custom provider, or file already removed — that's fine
    }

    log.engine.debug("provider-manager.deleteProvider: exit", { name });
  }

  // ── listProviders ──────────────────────────────────────────────

  listProviders(): ProviderStatus[] {
    log.engine.debug("provider-manager.listProviders: entry");

    const loader = getModelLoader();
    const registeredNames = new Set(this.registry.listProviders());

    const statuses = Object.entries(this.config.providers).map(
      ([name, entry]) => {
        // Resolve the profile key: config.profile → config name → raw name
        const profileKey = entry.profile ?? name;
        const modelDef = loader.get(profileKey);

        // Resolve the active model in preference order
        const activeModel =
          entry.activeModel ??
          entry.defaultModel ??
          modelDef?.defaultModel ??
          "unknown";

        // Derive circuit breaker health
        let health: ProviderStatus["health"] = "unconfigured";
        if (registeredNames.has(name)) {
          const breaker = (this.registry as any).breakers?.get(name);
          const breakerState: string | undefined = breaker?.getState?.();
          if (breakerState === "OPEN") {
            health = "OPEN";
          } else if (breakerState === "HALF_OPEN") {
            health = "HALF_OPEN";
          } else {
            health = "CLOSED";
          }
        }

        return {
          name,
          profile: profileKey,
          activeModel,
          isDefault: this.config.defaultProvider === name,
          health,
          source: loader.isSystemName(profileKey) ? "system" : "custom",
        } satisfies ProviderStatus;
      },
    );

    log.engine.debug("provider-manager.listProviders: exit", {
      count: statuses.length,
    });
    return statuses;
  }

  // ── testProvider ───────────────────────────────────────────────

  async testProvider(name: string): Promise<TestResult> {
    log.engine.debug("provider-manager.testProvider: entry", { name });
    const start = Date.now();

    try {
      const provider = this.registry.get(name);
      log.engine.debug("provider-manager.testProvider: step — calling healthCheck", {
        name,
      });
      const ok = await provider.healthCheck();
      const latencyMs = Date.now() - start;
      log.engine.debug("provider-manager.testProvider: exit", { name, ok, latencyMs });
      return { ok, latencyMs };
    } catch (err) {
      const latencyMs = Date.now() - start;
      const error = err instanceof Error ? err.message : String(err);
      log.engine.warn("provider-manager.testProvider: failed", err as Error, { name });
      return { ok: false, latencyMs, error };
    }
  }

  // ── isReservedOrDuplicate ──────────────────────────────────────

  /**
   * Helper for Telegram / TUI validation before showing a "create" form.
   * Returns true when the given name cannot be used for a new provider.
   */
  isReservedOrDuplicate(name: string, config: StackOwlConfig): boolean {
    return getModelLoader().isSystemName(name) || !!config.providers[name];
  }

  // ── Private helpers ────────────────────────────────────────────

  private async _writeModelFile(
    name: string,
    cfg: CustomProviderConfig,
  ): Promise<void> {
    log.engine.debug("provider-manager._writeModelFile: entry", { name });
    const lines = [
      `compatible: ${cfg.compatible}`,
      `url: "${cfg.url}"`,
      `availableModels: ${JSON.stringify(cfg.availableModels)}`,
      `defaultModel: "${cfg.defaultModel}"`,
    ];
    await writeFile(
      join(this.workspaceModelsDir, name),
      lines.join("\n"),
      "utf-8",
    );
    log.engine.debug("provider-manager._writeModelFile: exit", { name });
  }
}
