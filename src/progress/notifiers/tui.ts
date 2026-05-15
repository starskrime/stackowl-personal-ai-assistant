import { log } from "../../logger.js";
import type { UiBridge } from "../../cli/v2/events/bridge.js";
import type { ProgressNotifier } from "../types.js";

/**
 * TuiProgressNotifier — thin adapter from ProgressNotifier to UiBridge events.
 *
 * The TUI's spinner (ThinkingIndicator) already renders when generating:true.
 * This notifier adds two signals:
 *   - thinking.phrase: overrides the random language pick with the notifier-supplied phrase
 *   - thinking.tool:  shows tool status text while a tool is running
 *
 * start/stop do NOT emit turn.started / turn.committed — those are handled
 * by the existing cli-v2 adapter path and must not be duplicated.
 */
export class TuiProgressNotifier implements ProgressNotifier {
  private activeTurnIds = new Set<string>();

  constructor(private bridge: UiBridge) {}

  async start(phrase: string, turnId: string): Promise<void> {
    log.engine.debug("tui-progress-notifier: start: entry", { turnId, phraseLen: phrase.length });
    this.activeTurnIds.add(turnId);
    this.bridge.emit({ kind: "thinking.phrase", turnId, phrase });
    log.engine.debug("tui-progress-notifier: start: exit", { turnId });
  }

  async update(text: string, turnId: string): Promise<void> {
    if (!this.activeTurnIds.has(turnId)) {
      log.engine.debug("tui-progress-notifier: update: skipped — unknown turnId", { turnId });
      return;
    }
    log.engine.debug("tui-progress-notifier: update: entry", { turnId, text });
    this.bridge.emit({ kind: "thinking.tool", turnId, text });
    log.engine.debug("tui-progress-notifier: update: exit", { turnId });
  }

  async stop(turnId: string): Promise<void> {
    if (!this.activeTurnIds.has(turnId)) {
      log.engine.debug("tui-progress-notifier: stop: skipped — unknown turnId", { turnId });
      return;
    }
    log.engine.debug("tui-progress-notifier: stop: entry", { turnId });
    this.activeTurnIds.delete(turnId);
    // Clear the phrase override so ThinkingIndicator reverts to random fallback next time.
    this.bridge.emit({ kind: "thinking.phrase", turnId, phrase: "" });
    log.engine.debug("tui-progress-notifier: stop: exit", { turnId });
  }
}
