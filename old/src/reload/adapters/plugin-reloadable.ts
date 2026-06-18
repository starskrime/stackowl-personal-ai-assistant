/**
 * StackOwl — Plugin Reloadable Adapter
 *
 * Wraps a plugin as a ReloadableModule.
 * On reload: stop → destroy → teardown → load → init → start → ready
 */

import { existsSync } from "node:fs";
import type { ReloadableModule, ModuleSnapshot } from "../types.js";
import type { PluginLifecycleManager } from "../../plugins/lifecycle.js";
import type { PluginRegistry } from "../../plugins/registry.js";

export class PluginReloadable implements ReloadableModule {
  readonly kind = "plugin" as const;
  readonly dependsOn: string[];
  version = 0;

  constructor(
    readonly id: string,
    readonly filePath: string,
    private pluginName: string,
    private lifecycleManager: PluginLifecycleManager,
    private pluginRegistry: PluginRegistry,
    dependsOn: string[] = [],
  ) {
    this.dependsOn = dependsOn;
  }

  async validate(): Promise<boolean> {
    // Check manifest exists
    if (!existsSync(this.filePath)) return false;

    try {
      const raw = await (
        await import("node:fs/promises")
      ).readFile(this.filePath, "utf-8");
      const manifest = JSON.parse(raw);
      return !!manifest.name && !!manifest.version && !!manifest.entryPoint;
    } catch {
      return false;
    }
  }

  async load(): Promise<void> {
    // PluginLifecycleManager handles the full reload cycle
    await this.lifecycleManager.reloadPlugin(this.pluginName);
  }

  async unload(): Promise<void> {
    // Unload is handled as part of reloadPlugin()
    // This is a no-op because lifecycle manager handles the full cycle
  }

  snapshot(): ModuleSnapshot {
    const plugin = this.pluginRegistry.get(this.pluginName);
    return {
      moduleId: this.id,
      version: this.version,
      state: plugin
        ? {
            name: plugin.manifest.name,
            version: plugin.manifest.version,
            state: plugin.state,
          }
        : null,
      timestamp: Date.now(),
    };
  }

  async restore(_snapshot: ModuleSnapshot): Promise<void> {
    // Plugin rollback is complex — the lifecycle manager handles this
    // by catching errors during reload and leaving the old version running
    this.version = _snapshot.version;
  }
}
