/**
 * Typed config reload bus.
 * Subsystems subscribe at boot; patchConfig fires after successful disk write.
 * If a handler throws, patchConfig rolls back and propagates the error.
 */

import type { StackOwlConfig } from "./loader.js";

type ReloadHandler<K extends keyof StackOwlConfig> = (
  next: StackOwlConfig[K],
  prev: StackOwlConfig[K],
) => Promise<void>;

type HandlerMap = {
  [K in keyof StackOwlConfig]?: Array<ReloadHandler<K>>;
};

class ConfigReloadBus {
  private handlers: HandlerMap = {};

  on<K extends keyof StackOwlConfig>(section: K, handler: ReloadHandler<K>): void {
    if (!this.handlers[section]) this.handlers[section] = [];
    (this.handlers[section] as Array<ReloadHandler<K>>).push(handler);
  }

  async emit<K extends keyof StackOwlConfig>(
    section: K,
    next: StackOwlConfig[K],
    prev: StackOwlConfig[K],
  ): Promise<void> {
    const list = this.handlers[section] as Array<ReloadHandler<K>> | undefined;
    if (!list) return;
    for (const h of list) await h(next, prev);
  }

  /** Remove all handlers — used in tests. */
  reset(): void {
    this.handlers = {};
  }
}

export const configReloadBus = new ConfigReloadBus();
