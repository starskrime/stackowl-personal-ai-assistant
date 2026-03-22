/**
 * StackOwl — Plugin System
 *
 * Barrel exports for the plugin subsystem.
 */

export type {
  PluginManifest,
  PluginDependency,
  PluginState,
  PluginHooks,
  PluginInstance,
  ManagedPlugin,
} from "./types.js";
export { PluginSandbox } from "./sandbox.js";
export { ServiceRegistry } from "./services.js";
export { HookPipeline } from "./hook-pipeline.js";
export { PluginRegistry } from "./registry.js";
export { PluginLifecycleManager } from "./lifecycle.js";
