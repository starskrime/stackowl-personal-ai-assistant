/**
 * bridge.ts — the ONE translator.
 *
 * Translates engine StreamEvents and gateway bus events into UiEvents.
 * Nothing else may produce UiEvents.
 *
 * Forbidden: importing from src/engine/* directly.
 * Allowed: importing from src/gateway/events.ts stable re-exports only.
 */

import type { UiEvent } from "./UiEvent.js";

export type UiEventHandler = (event: UiEvent) => void;

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

  // Placeholder — wired in Phase 1 when the v2 adapter connects engine signals.
  // All translation logic (StreamEvent → UiEvent, gateway bus → UiEvent,
  // parliament DebateCallbacks → UiEvent, heartbeat emit → UiEvent)
  // will live exclusively in this class.
}

export const globalBridge = new UiBridge();
