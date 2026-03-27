/**
 * StackOwl — Config Reloadable Adapter
 *
 * Watches stackowl.config.json for changes.
 * On reload: parses, validates, and emits config:changed event
 * so dependent modules (plugins, tools, etc.) can react.
 */

import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import type { ReloadableModule, ModuleSnapshot } from "../types.js";
import type { EventBus } from "../../events/bus.js";
import type { StackOwlConfig } from "../../config/loader.js";
import { log } from "../../logger.js";

export class ConfigReloadable implements ReloadableModule {
  readonly kind = "config" as const;
  readonly dependsOn: string[] = [];
  version = 0;
  private currentConfig: StackOwlConfig | null = null;

  constructor(
    readonly id: string,
    readonly filePath: string,
    private eventBus: EventBus,
    initialConfig?: StackOwlConfig,
  ) {
    this.currentConfig = initialConfig ?? null;
  }

  async validate(): Promise<boolean> {
    if (!existsSync(this.filePath)) return false;

    try {
      const raw = await readFile(this.filePath, "utf-8");
      const parsed = JSON.parse(raw);
      // Basic shape validation
      return (
        typeof parsed === "object" &&
        parsed !== null &&
        typeof parsed.defaultProvider === "string" &&
        typeof parsed.providers === "object"
      );
    } catch {
      return false;
    }
  }

  async load(): Promise<void> {
    const raw = await readFile(this.filePath, "utf-8");
    const newConfig = JSON.parse(raw) as StackOwlConfig;

    // Compute diff (top-level keys)
    const changes: Record<string, unknown> = {};
    if (this.currentConfig) {
      for (const key of Object.keys(newConfig) as (keyof StackOwlConfig)[]) {
        if (
          JSON.stringify(newConfig[key]) !==
          JSON.stringify(this.currentConfig[key])
        ) {
          changes[key] = newConfig[key];
        }
      }
    }

    this.currentConfig = newConfig;

    if (Object.keys(changes).length > 0) {
      log.engine.info(
        `[ConfigReloadable] Config changed: ${Object.keys(changes).join(", ")}`,
      );
      // Plugins listening to onConfigChanged will be notified via HookPipeline
      this.eventBus.emit("reload:completed" as any, {
        moduleId: this.id,
        events: [
          {
            moduleId: this.id,
            kind: "config",
            action: "reload",
            success: true,
            rolledBack: false,
            durationMs: 0,
          },
        ],
      });
    }
  }

  async unload(): Promise<void> {
    // Config is always loaded; unload is a no-op
  }

  snapshot(): ModuleSnapshot {
    return {
      moduleId: this.id,
      version: this.version,
      state: this.currentConfig ? { ...this.currentConfig } : null,
      timestamp: Date.now(),
    };
  }

  async restore(snapshot: ModuleSnapshot): Promise<void> {
    this.currentConfig = snapshot.state as StackOwlConfig | null;
    this.version = snapshot.version;
  }

  /**
   * Get the current parsed config.
   */
  getConfig(): StackOwlConfig | null {
    return this.currentConfig;
  }
}
