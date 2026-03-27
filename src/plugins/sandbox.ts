/**
 * StackOwl — Plugin Sandbox
 *
 * Scoped access layer for plugins. Instead of receiving the full GatewayContext
 * (50+ fields), plugins get a sandbox with namespaced tool registration,
 * auto-cleanup event subscriptions, ACP messaging, and service injection.
 */

import type { ToolImplementation } from "../tools/registry.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { EventBus, EventType, EventPayloads } from "../events/bus.js";
import type { ServiceRegistry } from "./services.js";
import { Logger } from "../logger.js";

export class PluginSandbox {
  private registeredTools: string[] = [];
  private eventHandlers: Array<{
    type: EventType;
    handler: (...args: any[]) => any;
  }> = [];
  private providedServices: string[] = [];
  private acpHandlers: Array<{
    channel: string;
    handler: (msg: unknown) => Promise<void>;
  }> = [];
  readonly log: Logger;

  constructor(
    readonly pluginName: string,
    private toolRegistry: ToolRegistry,
    private eventBus: EventBus,
    private serviceRegistry: ServiceRegistry,
    private pluginConfig: Record<string, unknown>,
  ) {
    this.log = new Logger(`PLUGIN:${pluginName}`);
  }

  // ─── Namespaced Tool Registration ─────────────────────────────

  /**
   * Register a tool. Automatically namespaced: plugin_{pluginName}_{toolName}
   */
  registerTool(tool: ToolImplementation): void {
    const namespacedName = `plugin_${this.pluginName}_${tool.definition.name}`;
    const namespacedTool: ToolImplementation = {
      ...tool,
      definition: {
        ...tool.definition,
        name: namespacedName,
      },
      source: "plugin",
    };
    this.toolRegistry.register(namespacedTool);
    this.registeredTools.push(namespacedName);
    this.log.info(`Registered tool: ${namespacedName}`);
  }

  /**
   * Unregister a tool by its original (non-namespaced) name.
   */
  unregisterTool(name: string): void {
    const namespacedName = `plugin_${this.pluginName}_${name}`;
    this.toolRegistry.unregister(namespacedName);
    this.registeredTools = this.registeredTools.filter(
      (n) => n !== namespacedName,
    );
  }

  // ─── Scoped Event Subscription ────────────────────────────────

  /**
   * Subscribe to an event. Automatically cleaned up on teardown.
   */
  on<T extends EventType>(
    type: T,
    handler: (payload: EventPayloads[T]) => void | Promise<void>,
  ): void {
    this.eventBus.on(type, handler);
    this.eventHandlers.push({ type, handler });
  }

  /**
   * Emit an event through the bus.
   */
  emit<T extends EventType>(type: T, payload: EventPayloads[T]): void {
    this.eventBus.emit(type, payload);
  }

  // ─── ACP Messaging ────────────────────────────────────────────

  /**
   * Register a handler for incoming ACP messages on a channel.
   * Actual routing is wired up by the ACP system when available.
   */
  onMessage(channel: string, handler: (msg: unknown) => Promise<void>): void {
    this.acpHandlers.push({ channel, handler });
  }

  /**
   * Get registered ACP handlers (used by ACP router to wire up).
   */
  getACPHandlers(): Array<{
    channel: string;
    handler: (msg: unknown) => Promise<void>;
  }> {
    return [...this.acpHandlers];
  }

  // ─── Service Injection ────────────────────────────────────────

  /**
   * Provide a service for other plugins to consume.
   */
  provideService<T>(serviceName: string, implementation: T): void {
    this.serviceRegistry.provide(serviceName, this.pluginName, implementation);
    this.providedServices.push(serviceName);
  }

  /**
   * Consume a service provided by another plugin.
   */
  getService<T>(serviceName: string): T | undefined {
    return this.serviceRegistry.consume<T>(serviceName);
  }

  // ─── Plugin Config ────────────────────────────────────────────

  /**
   * Get plugin-scoped config value.
   */
  getConfig<T>(key: string): T | undefined {
    return this.pluginConfig[key] as T | undefined;
  }

  /**
   * Get all plugin config.
   */
  getAllConfig(): Record<string, unknown> {
    return { ...this.pluginConfig };
  }

  // ─── Teardown ─────────────────────────────────────────────────

  /**
   * Remove all registrations made by this plugin.
   * Called automatically during plugin destroy.
   */
  teardown(): void {
    // Remove tools
    for (const name of this.registeredTools) {
      this.toolRegistry.unregister(name);
    }
    this.registeredTools = [];

    // Remove event handlers
    for (const { type, handler } of this.eventHandlers) {
      this.eventBus.off(type, handler);
    }
    this.eventHandlers = [];

    // Remove services
    this.serviceRegistry.removeByProvider(this.pluginName);
    this.providedServices = [];

    // Clear ACP handlers
    this.acpHandlers = [];

    this.log.info("Sandbox teardown complete");
  }
}
