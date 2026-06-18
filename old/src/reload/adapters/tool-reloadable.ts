/**
 * StackOwl — Tool Reloadable Adapter
 *
 * Wraps a synthesized TypeScript tool as a ReloadableModule
 * for the HotReloadManager.
 */

import { pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import type { ReloadableModule, ModuleSnapshot } from "../types.js";
import type { ToolRegistry, ToolImplementation } from "../../tools/registry.js";

export class ToolReloadable implements ReloadableModule {
  readonly kind = "tool" as const;
  readonly dependsOn: string[];
  version = 0;
  private currentTool: ToolImplementation | null = null;

  constructor(
    readonly id: string,
    readonly filePath: string,
    private toolRegistry: ToolRegistry,
    dependsOn: string[] = [],
  ) {
    this.dependsOn = dependsOn;
  }

  async validate(): Promise<boolean> {
    // Check file exists and is importable
    if (!existsSync(this.filePath)) return false;

    try {
      const url = pathToFileURL(this.filePath).href + `?validate=${Date.now()}`;
      const mod = await import(url);
      const tool = mod.default || mod.tool;
      return !!(tool?.definition?.name && typeof tool.execute === "function");
    } catch {
      return false;
    }
  }

  async load(): Promise<void> {
    const url = pathToFileURL(this.filePath).href + `?t=${Date.now()}`;
    const mod = await import(url);
    const tool: ToolImplementation = mod.default || mod.tool;

    if (!tool?.definition?.name) {
      throw new Error(`Invalid tool export from ${this.filePath}`);
    }

    tool.source = "synthesized";
    this.toolRegistry.register(tool);
    this.currentTool = tool;
  }

  async unload(): Promise<void> {
    if (this.currentTool) {
      this.toolRegistry.unregister(this.currentTool.definition.name);
      this.currentTool = null;
    }
  }

  snapshot(): ModuleSnapshot {
    return {
      moduleId: this.id,
      version: this.version,
      state: this.currentTool
        ? {
            name: this.currentTool.definition.name,
            definition: { ...this.currentTool.definition },
          }
        : null,
      timestamp: Date.now(),
    };
  }

  async restore(snapshot: ModuleSnapshot): Promise<void> {
    // Re-register the old tool if we had one
    if (this.currentTool) {
      this.toolRegistry.register(this.currentTool);
    }
    this.version = snapshot.version;
  }
}
