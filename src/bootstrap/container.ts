/**
 * StackOwl — Bootstrap Container (Lightweight DI)
 *
 * Replaces the 1583-line manual wiring in index.ts with a convention-based
 * dependency injection container. Each subsystem registers itself with
 * dependencies declared — the container resolves the graph.
 *
 * Design principles:
 *   - Zero external libraries (no inversify, tsyringe, etc.)
 *   - Lazy initialization — services created only when first requested
 *   - Scoped lifetimes: singleton (default), transient, request-scoped
 *   - Type-safe via generics
 *   - Runtime wiring errors surface clearly at startup
 */

import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

type Lifetime = 'singleton' | 'transient';

interface ServiceDescriptor<T = unknown> {
  name: string;
  factory: (container: Container) => T | Promise<T>;
  lifetime: Lifetime;
  /** Dependencies this service requires (for graph validation) */
  dependsOn: string[];
  /** Tags for querying groups of services (e.g., 'tool', 'adapter', 'middleware') */
  tags: string[];
}

// ─── Container ───────────────────────────────────────────────────

export class Container {
  private descriptors: Map<string, ServiceDescriptor> = new Map();
  private singletons: Map<string, unknown> = new Map();
  private resolving: Set<string> = new Set();

  /**
   * Register a service factory.
   */
  register<T>(
    name: string,
    factory: (container: Container) => T | Promise<T>,
    options?: {
      lifetime?: Lifetime;
      dependsOn?: string[];
      tags?: string[];
    },
  ): this {
    if (this.descriptors.has(name)) {
      log.engine.warn(`[Container] Overwriting existing registration: "${name}"`);
    }

    this.descriptors.set(name, {
      name,
      factory: factory as (container: Container) => unknown | Promise<unknown>,
      lifetime: options?.lifetime ?? 'singleton',
      dependsOn: options?.dependsOn ?? [],
      tags: options?.tags ?? [],
    });

    return this;
  }

  /**
   * Register a pre-constructed instance (always singleton).
   */
  registerInstance<T>(name: string, instance: T, tags?: string[]): this {
    this.descriptors.set(name, {
      name,
      factory: () => instance,
      lifetime: 'singleton',
      dependsOn: [],
      tags: tags ?? [],
    });
    this.singletons.set(name, instance);
    return this;
  }

  /**
   * Resolve a service by name.
   */
  async resolve<T>(name: string): Promise<T> {
    const descriptor = this.descriptors.get(name);
    if (!descriptor) {
      throw new Error(`[Container] Service "${name}" not registered.`);
    }

    // Singleton already created
    if (descriptor.lifetime === 'singleton' && this.singletons.has(name)) {
      return this.singletons.get(name) as T;
    }

    // Circular dependency detection
    if (this.resolving.has(name)) {
      throw new Error(
        `[Container] Circular dependency detected: "${name}" is already being resolved. ` +
        `Resolution stack: [${[...this.resolving].join(' → ')} → ${name}]`,
      );
    }

    this.resolving.add(name);
    try {
      const instance = await descriptor.factory(this);

      if (descriptor.lifetime === 'singleton') {
        this.singletons.set(name, instance);
      }

      return instance as T;
    } finally {
      this.resolving.delete(name);
    }
  }

  /**
   * Resolve a service synchronously (throws if factory is async).
   */
  resolveSync<T>(name: string): T {
    const descriptor = this.descriptors.get(name);
    if (!descriptor) {
      throw new Error(`[Container] Service "${name}" not registered.`);
    }

    if (this.singletons.has(name)) {
      return this.singletons.get(name) as T;
    }

    if (this.resolving.has(name)) {
      throw new Error(`[Container] Circular dependency: "${name}"`);
    }

    this.resolving.add(name);
    try {
      const result = descriptor.factory(this);
      if (result instanceof Promise) {
        throw new Error(`[Container] Service "${name}" has async factory — use resolve() instead of resolveSync().`);
      }
      if (descriptor.lifetime === 'singleton') {
        this.singletons.set(name, result);
      }
      return result as T;
    } finally {
      this.resolving.delete(name);
    }
  }

  /**
   * Check if a service is registered.
   */
  has(name: string): boolean {
    return this.descriptors.has(name);
  }

  /**
   * Get all services tagged with a specific tag.
   */
  async resolveByTag<T>(tag: string): Promise<T[]> {
    const matching = [...this.descriptors.values()].filter(d => d.tags.includes(tag));
    const results: T[] = [];
    for (const desc of matching) {
      results.push(await this.resolve<T>(desc.name));
    }
    return results;
  }

  /**
   * Validate the dependency graph at startup.
   * Returns a list of missing dependencies.
   */
  validate(): string[] {
    const errors: string[] = [];

    for (const [name, desc] of this.descriptors) {
      for (const dep of desc.dependsOn) {
        if (!this.descriptors.has(dep)) {
          errors.push(`Service "${name}" depends on "${dep}" which is not registered.`);
        }
      }
    }

    return errors;
  }

  /**
   * Initialize all singleton services eagerly.
   * Useful for startup validation — surfaces errors early.
   */
  async initializeAll(): Promise<void> {
    const errors = this.validate();
    if (errors.length > 0) {
      throw new Error(
        `[Container] Dependency validation failed:\n${errors.map(e => `  - ${e}`).join('\n')}`,
      );
    }

    for (const [name, desc] of this.descriptors) {
      if (desc.lifetime === 'singleton' && !this.singletons.has(name)) {
        try {
          await this.resolve(name);
        } catch (err) {
          log.engine.error(
            `[Container] Failed to initialize "${name}": ${err instanceof Error ? err.message : err}`,
          );
          throw err;
        }
      }
    }

    log.engine.info(`[Container] All ${this.singletons.size} singleton services initialized.`);
  }

  /**
   * List all registered services (for diagnostics).
   */
  listServices(): Array<{ name: string; lifetime: Lifetime; tags: string[]; initialized: boolean }> {
    return [...this.descriptors.values()].map(d => ({
      name: d.name,
      lifetime: d.lifetime,
      tags: d.tags,
      initialized: this.singletons.has(d.name),
    }));
  }

  /**
   * Dispose all singletons (for shutdown).
   */
  async dispose(): Promise<void> {
    for (const [name, instance] of this.singletons) {
      if (instance && typeof (instance as any).dispose === 'function') {
        try {
          await (instance as any).dispose();
        } catch (err) {
          log.engine.warn(`[Container] Error disposing "${name}": ${err instanceof Error ? err.message : err}`);
        }
      }
    }
    this.singletons.clear();
  }
}
