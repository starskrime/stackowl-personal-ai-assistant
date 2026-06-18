import { log } from "../../logger.js";
import type { ProgressNotifier } from "../types.js";

/**
 * WebSocketProgressNotifier — stub implementation.
 * Full implementation pending WebSocket channel adapter buildout.
 *
 * Expected behavior when implemented:
 *   start()  → push { type: "thinking", phrase } to the connected client
 *   update() → push { type: "tool", text } to the connected client
 *   stop()   → push { type: "done" } to the connected client
 */
export class WebSocketProgressNotifier implements ProgressNotifier {
  async start(_phrase: string, turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: start (stub — not implemented)", { turnId });
  }

  async update(_text: string, turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: update (stub — not implemented)", { turnId });
  }

  async stop(turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: stop (stub — not implemented)", { turnId });
  }
}
