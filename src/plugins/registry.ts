/**
 * StackOwl — Plugin Registry
 *
 * Manages plugin registration, dependency resolution, and load ordering.
 * Topological sort ensures plugins load after their dependencies.
 */

import type { ManagedPlugin, PluginManifest, PluginInstance, PluginState } from "./types.js";
import type { PluginSandbox } from "./sandbox.js";
import { log } from "../logger.js";

export class PluginRegistry {
  private plugins = new Map<string, ManagedPlugin>();

  /**
   * Register a plugin with its manifest, instance, and sandbox.
   */
  register(
    manifest: PluginManifest,
    instance: PluginInstance,
    sandbox: PluginSandbox,
    pluginDir: string,
  ): void {
    if (this.plugins.has(manifest.name)) {
      log.engine.warn(`[PluginRegistry] Plugin "${manifest.name}" already registered, replacing`);
    }
    this.plugins.set(manifest.name, {
      manifest,
      instance,
      sandbox,
      state: "unloaded",
      loadedAt: Date.now(),
      pluginDir,
    });
    log.engine.info(
      `[PluginRegistry] Registered "${manifest.name}" v${manifest.version}`,
    );
  }

  /**
   * Unregister a plugin by name.
   */
  async unregister(name: string): Promise<void> {
    const plugin = this.plugins.get(name);
    if (!plugin) return;

    // Teardown sandbox (removes tools, events, services)
    plugin.sandbox.teardown();
    this.plugins.delete(name);
    log.engine.info(`[PluginRegistry] Unregistered "${name}"`);
  }

  /**
   * Get a managed plugin by name.
   */
  get(name: string): ManagedPlugin | undefined {
    return this.plugins.get(name);
  }

  /**
   * List all managed plugins.
   */
  list(): ManagedPlugin[] {
    return [...this.plugins.values()];
  }

  /**
   * Update the state of a plugin.
   */
  setState(name: string, state: PluginState): void {
    const plugin = this.plugins.get(name);
    if (plugin) {
      plugin.state = state;
    }
  }

  /**
   * Resolve the correct load order using topological sort.
   * Throws if circular dependencies are detected.
   */
  resolveLoadOrder(): string[] {
    const names = [...this.plugins.keys()];
    const visited = new Set<string>();
    const visiting = new Set<string>(); // cycle detection
    const order: string[] = [];

    const visit = (name: string) => {
      if (visited.has(name)) return;
      if (visiting.has(name)) {
        throw new Error(
          `[PluginRegistry] Circular dependency detected involving "${name}"`,
        );
      }

      visiting.add(name);
      const plugin = this.plugins.get(name);
      if (plugin) {
        const deps = plugin.manifest.requires.plugins ?? [];
        for (const dep of deps) {
          if (!dep.optional || this.plugins.has(dep.name)) {
            visit(dep.name);
          }
        }
      }
      visiting.delete(name);
      visited.add(name);
      order.push(name);
    };

    for (const name of names) {
      visit(name);
    }

    return order;
  }

  /**
   * Check if all required dependencies of a plugin are satisfied.
   */
  checkDependencies(name: string): { satisfied: boolean; missing: string[] } {
    const plugin = this.plugins.get(name);
    if (!plugin) return { satisfied: false, missing: [name] };

    const missing: string[] = [];
    const deps = plugin.manifest.requires.plugins ?? [];

    for (const dep of deps) {
      if (dep.optional) continue;
      if (!this.plugins.has(dep.name)) {
        missing.push(dep.name);
      }
    }

    // Check required services
    const requiredServices = plugin.manifest.requires.services ?? [];
    for (const svc of requiredServices) {
      // Services are checked at runtime, but we note missing ones
      // The ServiceRegistry handles actual resolution
      missing.push(`service:${svc}`);
    }

    // Check env vars
    const requiredEnv = plugin.manifest.requires.env ?? [];
    for (const envVar of requiredEnv) {
      if (!process.env[envVar]) {
        missing.push(`env:${envVar}`);
      }
    }

    // Bin checking is deferred to runtime (would need which/where)

    return { satisfied: missing.length === 0, missing };
  }
}
