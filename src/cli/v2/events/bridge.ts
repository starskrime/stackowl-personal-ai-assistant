/**
 * bridge.ts — the ONE translator.
 *
 * Translates engine StreamEvents and gateway bus events into UiEvents.
 * Nothing else may produce UiEvents.
 *
 * Forbidden: importing from src/engine/* directly.
 * Allowed: importing from src/gateway/events.ts stable re-exports only.
 */

import type { StreamEvent } from "../../../providers/base.js";
import type { UiEvent } from "./UiEvent.js";

export type UiEventHandler = (event: UiEvent) => void;

export interface OwlMeta {
  owlEmoji: string;
  owlName: string;
  owlRole?: string;
  estimatedCostUsd?: number;
}

export class UiBridge {
  private _handlers: UiEventHandler[] = [];

  subscribe(handler: UiEventHandler): () => void {
    this._handlers.push(handler);
    return () => {
      this._handlers = this._handlers.filter((h) => h !== handler);
    };
  }

  emit(event: UiEvent): void {
    for (const h of this._handlers) h(event);
  }

  /**
   * Translate a single StreamEvent into a UiEvent and emit it.
   *
   * Mapping:
   *   text_delta       → token.delta
   *   tool_start       → tool.requested
   *   tool_args_delta  → (ignored — args streaming not surfaced in TUI)
   *   tool_end         → tool.completed
   *   done             → turn.committed (accumulated text must be supplied by caller)
   */
  translateStreamEvent(
    turnId: string,
    event: StreamEvent,
    owlMeta: OwlMeta,
    /** Accumulated full text so far — required when event.type === "done" */
    fullText: string,
  ): void {
    switch (event.type) {
      case "text_delta":
        this.emit({ kind: "token.delta", turnId, text: event.content });
        break;

      case "tool_start":
        this.emit({
          kind: "tool.requested",
          toolCallId: event.toolCallId,
          turnId,
          toolName: event.toolName,
        });
        break;

      case "tool_args_delta":
        // Intentionally ignored — arg streaming is not surfaced in the TUI.
        break;

      case "tool_end":
        this.emit({
          kind: "tool.completed",
          toolCallId: event.toolCallId,
          elapsedMs: 0,
        });
        break;

      case "done":
        this.emit({
          kind: "turn.committed",
          turnId,
          text: fullText,
          usage: event.usage
            ? {
                promptTokens: event.usage.promptTokens,
                completionTokens: event.usage.completionTokens,
                costUsd: owlMeta.estimatedCostUsd ?? 0,
              }
            : undefined,
        });
        break;
    }
  }

  /**
   * Emit a turn.started event when the active owl is known (or changes).
   * Called before gateway.handle() begins and again when onOwlChange fires.
   */
  translateOwlChange(
    turnId: string,
    owlEmoji: string,
    owlName: string,
    owlRole?: string,
  ): void {
    this.emit({
      kind: "turn.started",
      turnId,
      owlId: owlName,
      owlName,
      owlEmoji,
      owlRole,
    });
  }
}

export const globalBridge = new UiBridge();
