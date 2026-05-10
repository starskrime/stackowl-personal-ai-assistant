/**
 * store.ts — Zustand vanilla store for TUI v2.
 *
 * Components read via selectors; only reducer.ts mutates state.
 */

import { createStore } from "zustand/vanilla";
import type { TurnsState } from "./slices/turns.js";
import type { ToolsState } from "./slices/tools.js";
import type { ParliamentState } from "./slices/parliament.js";
import type { HeartbeatState } from "./slices/heartbeat.js";
import type { SessionState } from "./slices/session.js";
import type { UiSliceState } from "./slices/ui.js";
import type { PaletteState } from "./slices/palette.js";
import { initialTurnsState } from "./slices/turns.js";
import { initialToolsState } from "./slices/tools.js";
import { initialParliamentState } from "./slices/parliament.js";
import { initialHeartbeatState } from "./slices/heartbeat.js";
import { initialSessionState } from "./slices/session.js";
import { initialUiSliceState } from "./slices/ui.js";
import { initialPaletteState } from "./slices/palette.js";

export interface UiState
  extends TurnsState,
    ToolsState,
    ParliamentState,
    HeartbeatState,
    SessionState,
    UiSliceState,
    PaletteState {}

export const initialState: UiState = {
  ...initialTurnsState,
  ...initialToolsState,
  ...initialParliamentState,
  ...initialHeartbeatState,
  ...initialSessionState,
  ...initialUiSliceState,
  ...initialPaletteState,
};

export const uiStore = createStore<UiState>(() => initialState);

export function applyToStore(updater: (state: UiState) => UiState): void {
  uiStore.setState(updater(uiStore.getState()));
}
