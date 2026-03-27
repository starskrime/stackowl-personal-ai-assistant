/**
 * StackOwl — Plugin Hook Pipeline
 *
 * Executes plugin hooks in priority order.
 * - "before" hooks: short-circuit on first non-null return
 * - "after" hooks: chain transformations sequentially
 */

import { log } from "../logger.js";

interface HookEntry {
  pluginName: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  handler: (...args: any[]) => Promise<any>;
  priority: number;
}

export class HookPipeline {
  private hooks = new Map<string, HookEntry[]>();

  /**
   * Register a hook from a plugin. Lower priority = runs first.
   */
  register(
    hookName: string,
    pluginName: string,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    handler: (...args: any[]) => Promise<any>,
    priority: number = 100,
  ): void {
    if (!this.hooks.has(hookName)) {
      this.hooks.set(hookName, []);
    }
    const entries = this.hooks.get(hookName)!;
    entries.push({ pluginName, handler, priority });
    // Keep sorted by priority (ascending)
    entries.sort((a, b) => a.priority - b.priority);
    log.engine.debug(
      `[HookPipeline] "${pluginName}" registered hook "${hookName}" (priority: ${priority})`,
    );
  }

  /**
   * Remove all hooks registered by a specific plugin.
   */
  removeByPlugin(pluginName: string): void {
    for (const [hookName, entries] of this.hooks) {
      const filtered = entries.filter((e) => e.pluginName !== pluginName);
      if (filtered.length === 0) {
        this.hooks.delete(hookName);
      } else {
        this.hooks.set(hookName, filtered);
      }
    }
  }

  /**
   * Execute "before" hooks in priority order.
   * Returns the first non-null result (short-circuit), or null if all return null.
   */
  async executeBefore<T>(
    hookName: string,
    ...args: unknown[]
  ): Promise<T | null> {
    const entries = this.hooks.get(hookName);
    if (!entries || entries.length === 0) return null;

    for (const entry of entries) {
      try {
        const result = await entry.handler(...args);
        if (result !== null && result !== undefined) {
          log.engine.debug(
            `[HookPipeline] "${hookName}" short-circuited by "${entry.pluginName}"`,
          );
          return result as T;
        }
      } catch (err) {
        log.engine.warn(
          `[HookPipeline] Error in "${entry.pluginName}" hook "${hookName}": ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return null;
  }

  /**
   * Execute "after" hooks in priority order, chaining the result.
   * Each hook receives the output of the previous hook.
   */
  async executeAfter<T>(
    hookName: string,
    initial: T,
    ...args: unknown[]
  ): Promise<T> {
    const entries = this.hooks.get(hookName);
    if (!entries || entries.length === 0) return initial;

    let current = initial;
    for (const entry of entries) {
      try {
        const result = await entry.handler(current, ...args);
        if (result !== undefined) {
          current = result as T;
        }
      } catch (err) {
        log.engine.warn(
          `[HookPipeline] Error in "${entry.pluginName}" after-hook "${hookName}": ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return current;
  }

  /**
   * Check if any hooks are registered for a given hook name.
   */
  has(hookName: string): boolean {
    const entries = this.hooks.get(hookName);
    return !!entries && entries.length > 0;
  }

  /**
   * List all registered hook names and their plugin counts.
   */
  listAll(): Array<{ hookName: string; plugins: string[] }> {
    return [...this.hooks.entries()].map(([hookName, entries]) => ({
      hookName,
      plugins: entries.map((e) => e.pluginName),
    }));
  }
}
