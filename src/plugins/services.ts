/**
 * StackOwl — Service Registry
 *
 * Enables typed service injection between plugins.
 * Plugin A can provide a service, Plugin B can consume it — no direct imports.
 */

import { log } from "../logger.js";

interface ServiceEntry {
  provider: string;
  implementation: unknown;
}

export class ServiceRegistry {
  private services = new Map<string, ServiceEntry>();

  /**
   * Register a service implementation from a plugin.
   */
  provide<T>(serviceName: string, providerPlugin: string, implementation: T): void {
    if (this.services.has(serviceName)) {
      const existing = this.services.get(serviceName)!;
      log.engine.warn(
        `[ServiceRegistry] Service "${serviceName}" already provided by "${existing.provider}", overwriting with "${providerPlugin}"`,
      );
    }
    this.services.set(serviceName, { provider: providerPlugin, implementation });
    log.engine.info(`[ServiceRegistry] "${providerPlugin}" provides service "${serviceName}"`);
  }

  /**
   * Consume a service by name. Returns undefined if not registered.
   */
  consume<T>(serviceName: string): T | undefined {
    const entry = this.services.get(serviceName);
    return entry?.implementation as T | undefined;
  }

  /**
   * Check if a service is available.
   */
  has(serviceName: string): boolean {
    return this.services.has(serviceName);
  }

  /**
   * Get which plugin provides a service.
   */
  getProvider(serviceName: string): string | undefined {
    return this.services.get(serviceName)?.provider;
  }

  /**
   * Remove all services provided by a specific plugin (on unload/destroy).
   */
  removeByProvider(pluginName: string): number {
    let removed = 0;
    for (const [name, entry] of this.services) {
      if (entry.provider === pluginName) {
        this.services.delete(name);
        removed++;
      }
    }
    if (removed > 0) {
      log.engine.info(`[ServiceRegistry] Removed ${removed} services from "${pluginName}"`);
    }
    return removed;
  }

  /**
   * List all registered services.
   */
  listAll(): Array<{ name: string; provider: string }> {
    return [...this.services.entries()].map(([name, entry]) => ({
      name,
      provider: entry.provider,
    }));
  }
}
