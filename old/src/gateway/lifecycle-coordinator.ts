import { log } from "../logger.js";

export interface ILifecycleCoordinator {
  register(name: string, cb: () => Promise<void>): void;
  startTimer(name: string, intervalMs: number, fn: () => Promise<void>): void;
  stopTimer(name: string): void;
  shutdown(): Promise<void>;
}

export class LifecycleCoordinator implements ILifecycleCoordinator {
  private readonly callbacks = new Map<string, () => Promise<void>>();
  private readonly timers = new Map<string, NodeJS.Timeout>();
  private shuttingDown = false;

  constructor() {
    process.once("SIGINT", () => { this.shutdown().finally(() => process.exit(0)); });
    process.once("SIGTERM", () => { this.shutdown().finally(() => process.exit(0)); });
    process.once("beforeExit", () => void this.shutdown());
  }

  register(name: string, cb: () => Promise<void>): void {
    log.gateway.debug("LifecycleCoordinator.register: entry", { name });
    this.callbacks.set(name, cb);
    log.gateway.debug("LifecycleCoordinator.register: exit", { name, totalCallbacks: this.callbacks.size });
  }

  startTimer(name: string, intervalMs: number, fn: () => Promise<void>): void {
    log.gateway.debug("LifecycleCoordinator.startTimer: entry", { name, intervalMs });
    if (this.timers.has(name)) {
      log.gateway.warn("LifecycleCoordinator.startTimer: duplicate name ignored", { name });
      return;
    }
    const id = setInterval(() => void fn(), intervalMs);
    this.timers.set(name, id);
    log.gateway.debug("LifecycleCoordinator.startTimer: exit", { name });
  }

  stopTimer(name: string): void {
    log.gateway.debug("LifecycleCoordinator.stopTimer: entry", { name });
    const id = this.timers.get(name);
    if (id !== undefined) {
      clearInterval(id);
      this.timers.delete(name);
    }
    log.gateway.debug("LifecycleCoordinator.stopTimer: exit", { name });
  }

  async shutdown(): Promise<void> {
    if (this.shuttingDown) return;
    this.shuttingDown = true;
    const names = [...this.callbacks.keys()].reverse(); // LIFO
    log.gateway.info("LifecycleCoordinator.shutdown: entry", { callbackCount: names.length });

    // Stop timers first — prevent new work from being scheduled during drain
    for (const name of [...this.timers.keys()]) {
      this.stopTimer(name);
    }

    for (const name of names) {
      log.gateway.debug("LifecycleCoordinator.shutdown: running callback", { name });
      try {
        await this.callbacks.get(name)!();
      } catch (err) {
        log.gateway.error("LifecycleCoordinator.shutdown: callback failed", err as Error, { name });
      }
    }

    log.gateway.info("LifecycleCoordinator.shutdown: complete");
  }
}
