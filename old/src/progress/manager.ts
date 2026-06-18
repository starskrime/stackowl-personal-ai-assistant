import { log } from "../logger.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { ProgressNotifier } from "./types.js";
import { getToolStatusPhrase } from "../shared/progress.js";

/**
 * ProgressManager — subscribes to GatewayEventBus and fans out progress
 * events to all registered ProgressNotifier implementations.
 *
 * Channel adapters:
 *   1. register(notifier) at startup
 *   2. call notifyStart(phrase, turnId) before gateway.handle()
 *   3. call notifyStop(turnId) after gateway.handle() resolves
 *
 * tool:start events are intercepted automatically and fanned out as update().
 */
export class ProgressManager {
  private notifiers = new Set<ProgressNotifier>();

  constructor(eventBus: GatewayEventBus) {
    log.engine.debug("progress-manager: init");

    eventBus.on("tool:start", (e) => {
      log.engine.debug("progress-manager: tool:start", { toolName: e.toolName, turnId: e.turnId });
      const phrase = getToolStatusPhrase(e.toolName);
      void this._fanOutUpdate(phrase, e.turnId);
    });
  }

  register(notifier: ProgressNotifier): void {
    log.engine.debug("progress-manager: register", { total: this.notifiers.size + 1 });
    this.notifiers.add(notifier);
    log.engine.debug("progress-manager: register: exit", { total: this.notifiers.size });
  }

  unregister(notifier: ProgressNotifier): void {
    log.engine.debug("progress-manager: unregister", { total: this.notifiers.size - 1 });
    this.notifiers.delete(notifier);
    log.engine.debug("progress-manager: unregister: exit", { total: this.notifiers.size });
  }

  async notifyStart(phrase: string, turnId: string): Promise<void> {
    log.engine.debug("progress-manager: notifyStart", { turnId, phraseLen: phrase.length });
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.start(phrase, turnId).catch((err) => {
          log.engine.error("progress-manager: notifyStart fan-out error", err as Error, { turnId });
        }),
      ),
    );
  }

  async notifyStop(turnId: string): Promise<void> {
    log.engine.debug("progress-manager: notifyStop", { turnId });
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.stop(turnId).catch((err) => {
          log.engine.error("progress-manager: notifyStop fan-out error", err as Error, { turnId });
        }),
      ),
    );
  }

  private async _fanOutUpdate(text: string, turnId: string): Promise<void> {
    log.engine.debug("progress-manager: _fanOutUpdate: entry", { text, turnId, notifierCount: this.notifiers.size });
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.update(text, turnId).catch((err) => {
          log.engine.error("progress-manager: fanOutUpdate error", err as Error, { turnId });
        }),
      ),
    );
    log.engine.debug("progress-manager: _fanOutUpdate: exit", { turnId });
  }
}
