/**
 * StackOwl — Dependency Graph
 *
 * Tracks dependencies between reloadable modules.
 * When a module changes, computes the topological reload order
 * for that module and all its transitive dependents.
 */

import type { ReloadableModule } from "./types.js";

export class DependencyGraph {
  /** module ID → module instance */
  private nodes = new Map<string, ReloadableModule>();
  /** module ID → set of module IDs it depends on */
  private edges = new Map<string, Set<string>>();
  /** module ID → set of module IDs that depend on it (reverse edges) */
  private reverseEdges = new Map<string, Set<string>>();

  /**
   * Register a module in the graph.
   */
  register(module: ReloadableModule): void {
    this.nodes.set(module.id, module);
    this.edges.set(module.id, new Set(module.dependsOn));

    // Build reverse edges
    if (!this.reverseEdges.has(module.id)) {
      this.reverseEdges.set(module.id, new Set());
    }
    for (const dep of module.dependsOn) {
      if (!this.reverseEdges.has(dep)) {
        this.reverseEdges.set(dep, new Set());
      }
      this.reverseEdges.get(dep)!.add(module.id);
    }
  }

  /**
   * Remove a module from the graph.
   */
  unregister(id: string): void {
    // Remove from reverse edges of dependencies
    const deps = this.edges.get(id);
    if (deps) {
      for (const dep of deps) {
        this.reverseEdges.get(dep)?.delete(id);
      }
    }

    // Remove reverse edges pointing to this module
    const dependents = this.reverseEdges.get(id);
    if (dependents) {
      for (const dependent of dependents) {
        this.edges.get(dependent)?.delete(id);
      }
    }

    this.nodes.delete(id);
    this.edges.delete(id);
    this.reverseEdges.delete(id);
  }

  /**
   * Get a module by ID.
   */
  get(id: string): ReloadableModule | undefined {
    return this.nodes.get(id);
  }

  /**
   * Given a changed module, return the topological order of
   * modules that need reloading (the changed module + all dependents).
   */
  getReloadOrder(changedId: string): string[] {
    // Collect all transitive dependents
    const affected = new Set<string>([changedId]);
    const queue = [changedId];

    while (queue.length > 0) {
      const current = queue.shift()!;
      const dependents = this.reverseEdges.get(current);
      if (dependents) {
        for (const dep of dependents) {
          if (!affected.has(dep)) {
            affected.add(dep);
            queue.push(dep);
          }
        }
      }
    }

    // Topological sort of affected nodes
    const visited = new Set<string>();
    const order: string[] = [];

    const visit = (id: string) => {
      if (visited.has(id) || !affected.has(id)) return;
      visited.add(id);

      // Visit dependencies first
      const deps = this.edges.get(id);
      if (deps) {
        for (const dep of deps) {
          if (affected.has(dep)) {
            visit(dep);
          }
        }
      }
      order.push(id);
    };

    for (const id of affected) {
      visit(id);
    }

    return order;
  }

  /**
   * Get all transitive dependents of a module.
   */
  getDependents(id: string): string[] {
    const result = new Set<string>();
    const queue = [id];

    while (queue.length > 0) {
      const current = queue.shift()!;
      const dependents = this.reverseEdges.get(current);
      if (dependents) {
        for (const dep of dependents) {
          if (!result.has(dep)) {
            result.add(dep);
            queue.push(dep);
          }
        }
      }
    }

    return [...result];
  }

  /**
   * Detect circular dependencies.
   */
  hasCycle(): boolean {
    const visited = new Set<string>();
    const visiting = new Set<string>();

    const dfs = (id: string): boolean => {
      if (visiting.has(id)) return true;
      if (visited.has(id)) return false;

      visiting.add(id);
      const deps = this.edges.get(id);
      if (deps) {
        for (const dep of deps) {
          if (dfs(dep)) return true;
        }
      }
      visiting.delete(id);
      visited.add(id);
      return false;
    };

    for (const id of this.nodes.keys()) {
      if (dfs(id)) return true;
    }

    return false;
  }

  /**
   * List all tracked module IDs.
   */
  listAll(): string[] {
    return [...this.nodes.keys()];
  }
}
