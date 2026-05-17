/**
 * fixtures/events.ts — UiEvent recorder for TUI v2 unit tests.
 *
 * captureEvents() subscribes to globalBridge before invoking the callback
 * and collects every UiEvent emitted during it. The subscription is torn
 * down in a finally block, so events from one call never leak into another.
 */

import { globalBridge } from "../../../../src/cli/v2/events/bridge.js";
import { resetStore } from "../../../../src/cli/v2/state/store.js";
import type { UiEvent } from "../../../../src/cli/v2/events/UiEvent.js";

/**
 * Run `fn` and return every UiEvent emitted on globalBridge during the call.
 * The store is reset before `fn` is invoked so each call starts from a known state.
 */
export function captureEvents(fn: () => void): UiEvent[] {
  const captured: UiEvent[] = [];
  const unsub = globalBridge.subscribe((event) => {
    captured.push(event);
  });
  resetStore();
  try {
    fn();
  } finally {
    unsub();
  }
  return captured;
}
