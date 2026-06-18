/**
 * StackOwl — Output Filter
 *
 * Applies output verbosity mode to gateway callbacks before they reach the engine.
 * The engine always emits all events; this filter decides which ones reach the channel.
 *
 * Modes:
 *   "normal" — users see only the final answer. onProgress is suppressed entirely;
 *              onStreamEvent passes through text_delta and done only.
 *   "debug"  — full visibility: tool start/finish, _Thinking..._ headers, all events.
 */

import type { StreamEvent } from "../providers/base.js";
import type { GatewayCallbacks } from "./types.js";

export type OutputMode = "normal" | "debug";

/**
 * Resolve the effective output mode from config, with backwards-compat for
 * the deprecated `suppressThinkingMessages` boolean.
 */
export function resolveOutputMode(gateway?: {
  outputMode?: OutputMode;
  suppressThinkingMessages?: boolean;
}): OutputMode {
  if (gateway?.outputMode) return gateway.outputMode;
  // Backwards compat: suppressThinkingMessages=false → debug
  if (gateway?.suppressThinkingMessages === false) return "debug";
  return "normal";
}

export class OutputFilter {
  constructor(private mode: OutputMode) {}

  /**
   * Return a filtered version of the callbacks.
   * The original callbacks are never mutated.
   */
  apply(callbacks: GatewayCallbacks): GatewayCallbacks {
    if (this.mode === "debug") return callbacks;

    // normal mode: suppress onProgress entirely; filter onStreamEvent to text + done
    return {
      ...callbacks,
      onProgress: undefined,
      onStreamEvent: callbacks.onStreamEvent
        ? this.wrapStreamEvent(callbacks.onStreamEvent)
        : undefined,
    };
  }

  private wrapStreamEvent(
    fn: (event: StreamEvent) => Promise<void>,
  ): (event: StreamEvent) => Promise<void> {
    return async (event: StreamEvent) => {
      if (event.type === "text_delta" || event.type === "done") {
        await fn(event);
      }
      // tool_start, tool_args_delta, tool_end — dropped in normal mode
    };
  }
}
