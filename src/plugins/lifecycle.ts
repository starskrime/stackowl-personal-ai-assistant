/**
 * StackOwl — Plugin Lifecycle Manager
 *
 * Manages the full plugin lifecycle:
 *   discover → load manifest → init(sandbox) → start() → ready() → stop() → destroy()
 *
 * The `ready()` phase is called AFTER all plugins have started, enabling cross-plugin init.
 */

import { readFile } from "node:fs/promises";
import { existsSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import type { PluginManifest, PluginInstance, ManagedPlugin } from "./types.js";
import { PluginRegistry } from "./registry.js";
import { PluginSandbox } from "./sandbox.js";
import { ServiceRegistry } from "./services.js";
import { HookPipeline } from "./hook-pipeline.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { EventBus } from "../events/bus.js";
import { log } from "../logger.js";

export class PluginLifecycleManager {
  constructor(
    private registry: PluginRegistry,
    private serviceRegistry: ServiceRegistry,
    private hookPipeline: HookPipeline,
    private toolRegistry: ToolRegistry,
    private eventBus: EventBus,
  ) {}

  /**
   * Discover and register all plugins from a directory.
   * Each subdirectory with a stackowl.plugin.json is a plugin.
   */
  async loadAll(pluginDir: string): Promise<number> {
    const resolvedDir = resolve(pluginDir);
    if (!existsSync(resolvedDir)) {
      log.engine.info(`[PluginLifecycle] Plugin directory not found: ${resolvedDir}`);
      return 0;
    }

    const entries = readdirSync(resolvedDir, { withFileTypes: true });
    let loaded = 0;

    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const manifestPath = join(resolvedDir, entry.name, "stackowl.plugin.json");
      if (!existsSync(manifestPath)) continue;

      try {
        await this.loadPlugin(manifestPath);
        loaded++;
      } catch (err) {
        log.engine.error(
          `[PluginLifecycle] Failed to load plugin from ${entry.name}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    log.engine.info(`[PluginLifecycle] Loaded ${loaded} plugins from ${resolvedDir}`);
    return loaded;
  }

  /**
   * Load a single plugin from its manifest path.
   */
  async loadPlugin(manifestPath: string): Promise<void> {
    const pluginDir = resolve(manifestPath, "..");
    const raw = await readFile(manifestPath, "utf-8");
    const manifest: PluginManifest = JSON.parse(raw);

    // Validate manifest basics
    if (!manifest.name || !manifest.version || !manifest.entryPoint) {
      throw new Error(`Invalid manifest: name, version, and entryPoint are required`);
    }

    // Load the plugin module
    const entryPath = join(pluginDir, manifest.entryPoint);
    if (!existsSync(entryPath)) {
      throw new Error(`Entry point not found: ${entryPath}`);
    }

    const entryUrl = pathToFileURL(entryPath).href + `?t=${Date.now()}`;
    const mod = await import(entryUrl);
    const PluginClass = mod.default || mod[manifest.name] || mod.Plugin;

    if (!PluginClass) {
      throw new Error(`No default export or Plugin class found in ${entryPath}`);
    }

    // Instantiate
    const instance: PluginInstance =
      typeof PluginClass === "function" ? new PluginClass() : PluginClass;

    // Create sandbox
    const pluginConfig = manifest.configSchema ? {} : {};
    const sandbox = new PluginSandbox(
      manifest.name,
      this.toolRegistry,
      this.eventBus,
      this.serviceRegistry,
      pluginConfig,
    );

    // Register
    this.registry.register(manifest, instance, sandbox, pluginDir);

    // Emit event
    this.eventBus.emit("plugin:loaded" as any, {
      name: manifest.name,
      version: manifest.version,
    });
  }

  /**
   * Initialize and start all registered plugins in dependency order.
   */
  async startAll(): Promise<void> {
    const order = this.registry.resolveLoadOrder();
    log.engine.info(`[PluginLifecycle] Starting plugins in order: ${order.join(" → ")}`);

    // Phase 1: init all
    for (const name of order) {
      const plugin = this.registry.get(name);
      if (!plugin) continue;

      // Check dependencies
      const depCheck = this.registry.checkDependencies(name);
      if (!depCheck.satisfied) {
        log.engine.warn(
          `[PluginLifecycle] Skipping "${name}": missing deps: ${depCheck.missing.join(", ")}`,
        );
        this.registry.setState(name, "error");
        continue;
      }

      try {
        this.registry.setState(name, "initializing");
        await plugin.instance.init(plugin.sandbox);
        this.registry.setState(name, "initialized");
      } catch (err) {
        log.engine.error(
          `[PluginLifecycle] "${name}" init failed: ${err instanceof Error ? err.message : String(err)}`,
        );
        this.registry.setState(name, "error");
      }
    }

    // Phase 2: start all initialized plugins
    for (const name of order) {
      const plugin = this.registry.get(name);
      if (!plugin || plugin.state !== "initialized") continue;

      try {
        this.registry.setState(name, "starting");
        await plugin.instance.start();
        this.installHooks(plugin);
        this.registry.setState(name, "ready");

        this.eventBus.emit("plugin:started" as any, { name });
        log.engine.info(`[PluginLifecycle] "${name}" started`);
      } catch (err) {
        log.engine.error(
          `[PluginLifecycle] "${name}" start failed: ${err instanceof Error ? err.message : String(err)}`,
        );
        this.registry.setState(name, "error");
      }
    }

    // Phase 3: call ready() on all started plugins
    for (const name of order) {
      const plugin = this.registry.get(name);
      if (!plugin || plugin.state !== "ready") continue;

      if (plugin.instance.ready) {
        try {
          await plugin.instance.ready();
        } catch (err) {
          log.engine.warn(
            `[PluginLifecycle] "${name}" ready() failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }
    }
  }

  /**
   * Stop all plugins in reverse order.
   */
  async stopAll(): Promise<void> {
    const order = this.registry.resolveLoadOrder().reverse();

    for (const name of order) {
      const plugin = this.registry.get(name);
      if (!plugin || plugin.state === "unloaded" || plugin.state === "error") continue;

      try {
        this.registry.setState(name, "stopping");
        this.removeHooks(plugin);
        await plugin.instance.stop();
        this.registry.setState(name, "stopped");

        this.registry.setState(name, "destroying");
        await plugin.instance.destroy();
        plugin.sandbox.teardown();
        this.registry.setState(name, "unloaded");

        this.eventBus.emit("plugin:stopped" as any, { name });
        log.engine.info(`[PluginLifecycle] "${name}" stopped and destroyed`);
      } catch (err) {
        log.engine.error(
          `[PluginLifecycle] Error stopping "${name}": ${err instanceof Error ? err.message : String(err)}`,
        );
        this.registry.setState(name, "error");
      }
    }
  }

  /**
   * Reload a single plugin: stop → destroy → load → init → start → ready
   */
  async reloadPlugin(name: string): Promise<void> {
    const plugin = this.registry.get(name);
    if (!plugin) {
      log.engine.warn(`[PluginLifecycle] Cannot reload unknown plugin "${name}"`);
      return;
    }

    const manifestPath = join(plugin.pluginDir, "stackowl.plugin.json");
    log.engine.info(`[PluginLifecycle] Reloading plugin "${name}"...`);

    // Stop + destroy existing
    try {
      this.removeHooks(plugin);
      if (plugin.state === "ready" || plugin.state === "starting") {
        await plugin.instance.stop();
      }
      await plugin.instance.destroy();
      plugin.sandbox.teardown();
    } catch (err) {
      log.engine.warn(
        `[PluginLifecycle] Error during "${name}" teardown: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    // Unregister
    await this.registry.unregister(name);

    // Re-load
    await this.loadPlugin(manifestPath);
    const reloaded = this.registry.get(name);
    if (!reloaded) return;

    // Re-init → start → ready
    try {
      this.registry.setState(name, "initializing");
      await reloaded.instance.init(reloaded.sandbox);
      this.registry.setState(name, "initialized");

      this.registry.setState(name, "starting");
      await reloaded.instance.start();
      this.installHooks(reloaded);
      this.registry.setState(name, "ready");

      if (reloaded.instance.ready) {
        await reloaded.instance.ready();
      }

      this.eventBus.emit("plugin:started" as any, { name });
      log.engine.info(`[PluginLifecycle] "${name}" reloaded successfully`);
    } catch (err) {
      log.engine.error(
        `[PluginLifecycle] "${name}" reload failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      this.registry.setState(name, "error");
    }
  }

  /**
   * Install plugin hooks into the hook pipeline.
   */
  private installHooks(plugin: ManagedPlugin): void {
    const hooks = plugin.instance.hooks;
    if (!hooks) return;

    const name = plugin.manifest.name;

    if (hooks.beforeEngine) {
      this.hookPipeline.register("beforeEngine", name, hooks.beforeEngine.bind(hooks));
    }
    if (hooks.afterEngine) {
      this.hookPipeline.register("afterEngine", name, hooks.afterEngine.bind(hooks));
    }
    if (hooks.beforeToolCall) {
      this.hookPipeline.register("beforeToolCall", name, hooks.beforeToolCall.bind(hooks));
    }
    if (hooks.afterToolCall) {
      this.hookPipeline.register("afterToolCall", name, hooks.afterToolCall.bind(hooks));
    }
    if (hooks.onSessionCreated) {
      this.hookPipeline.register("onSessionCreated", name, hooks.onSessionCreated.bind(hooks));
    }
    if (hooks.onSessionEnded) {
      this.hookPipeline.register("onSessionEnded", name, hooks.onSessionEnded.bind(hooks));
    }
    if (hooks.onEvolutionTriggered) {
      this.hookPipeline.register("onEvolutionTriggered", name, hooks.onEvolutionTriggered.bind(hooks));
    }
    if (hooks.onConfigChanged) {
      this.hookPipeline.register("onConfigChanged", name, hooks.onConfigChanged.bind(hooks));
    }
  }

  /**
   * Remove plugin hooks from the hook pipeline.
   */
  private removeHooks(plugin: ManagedPlugin): void {
    this.hookPipeline.removeByPlugin(plugin.manifest.name);
  }
}
