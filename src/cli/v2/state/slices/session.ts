import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface SessionSummary {
  sessionId: string;
  title: string;
  lastActiveAt: number;
}

export interface SessionState {
  activeSessionId: string | null;
  recentSessions: SessionSummary[];
}

export const initialSessionState: SessionState = {
  activeSessionId: null,
  recentSessions: [],
};

export function applySessionEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "session.changed":
      return { ...state, activeSessionId: event.sessionId };
    default:
      return state;
  }
}
