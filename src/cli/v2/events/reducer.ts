/**
 * reducer.ts — the ONE reducer.
 *
 * (state, UiEvent) => state — only place that mutates UI state.
 * Pure function; all slices delegate here.
 */

import type { UiState } from "../state/store.js";
import type { UiEvent } from "./UiEvent.js";
import { applyTurnsEvent } from "../state/slices/turns.js";
import { applyToolsEvent } from "../state/slices/tools.js";
import { applyParliamentEvent } from "../state/slices/parliament.js";
import { applyHeartbeatEvent } from "../state/slices/heartbeat.js";
import { applySessionEvent } from "../state/slices/session.js";
import { applyUiEvent } from "../state/slices/ui.js";
import { applyPaletteEvent } from "../state/slices/palette.js";

export function reduce(state: UiState, event: UiEvent): UiState {
  let next = state;
  next = applyTurnsEvent(next, event);
  next = applyToolsEvent(next, event);
  next = applyParliamentEvent(next, event);
  next = applyHeartbeatEvent(next, event);
  next = applySessionEvent(next, event);
  next = applyUiEvent(next, event);
  next = applyPaletteEvent(next, event);
  return next;
}
