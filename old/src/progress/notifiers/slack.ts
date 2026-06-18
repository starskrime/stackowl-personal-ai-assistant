import { log } from "../../logger.js";
import type { ProgressNotifier } from "../types.js";

/**
 * SlackProgressNotifier — stub implementation.
 * Full implementation pending Slack channel adapter buildout.
 *
 * Expected behavior when implemented:
 *   start()  → add a reaction emoji (e.g. ⏳) to the user's message
 *   update() → post/update an ephemeral status message in the thread
 *   stop()   → remove the reaction emoji
 */
export class SlackProgressNotifier implements ProgressNotifier {
  async start(_phrase: string, turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: start (stub — not implemented)", { turnId });
  }

  async update(_text: string, turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: update (stub — not implemented)", { turnId });
  }

  async stop(turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: stop (stub — not implemented)", { turnId });
  }
}
