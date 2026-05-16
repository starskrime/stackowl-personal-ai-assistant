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
    const onExit = () => void this.shutdown();
    process.once("exit", onExit);
    process.once("SIGINT", () => { void this.shutdown(); process.exit(0); });
    process.once("SIGTERM", () => { void this.shutdown(); process.exit(0); });
    process.once("beforeExit", onExit);
  }

  register(name: string, cb: () => Promise<void>): void {
    log.gateway.debug("LifecycleCoordinator.register: entry", { name });
    this.callbacks.set(name, cb);
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

    for (const name of names) {
      log.gateway.debug("LifecycleCoordinator.shutdown: running callback", { name });
      try {
        await this.callbacks.get(name)!();
      } catch (err) {
        log.gateway.error("LifecycleCoordinator.shutdown: callback failed", err as Error, { name });
      }
    }

    for (const name of [...this.timers.keys()]) {
      this.stopTimer(name);
    }

    log.gateway.info("LifecycleCoordinator.shutdown: complete");
  }
}
