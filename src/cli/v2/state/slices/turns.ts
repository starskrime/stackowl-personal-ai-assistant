import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface Turn {
  turnId: string;
  role: "user" | "assistant";
  owlId?: string;
  owlName?: string;
  owlEmoji?: string;
  owlRole?: string;
  text: string;
  committed: boolean;
  timestamp: number;
}

export interface TurnsState {
  /** Ring buffer: last 200 turns kept in memory; older flushed to SQLite. */
  turns: Turn[];
  /** Live streaming turn (not yet in `turns`). */
  liveTurn: Turn | null;
}

export const initialTurnsState: TurnsState = {
  turns: [],
  liveTurn: null,
};

/** Maximum turns to hold in memory before the oldest are trimmed. */
const MAX_TURNS = 200;

export function applyTurnsEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "user.message": {
      const userTurn: Turn = {
        turnId: event.turnId,
        role: "user",
        text: event.text,
        committed: true,
        timestamp: Date.now(),
      };
      const turns = [...state.turns, userTurn].slice(-MAX_TURNS);
      return { ...state, turns };
    }

    case "turn.started": {
      const live: Turn = {
        turnId: event.turnId,
        role: "assistant",
        owlId: event.owlId,
        owlName: event.owlName,
        owlEmoji: event.owlEmoji,
        owlRole: event.owlRole,
        text: "",
        committed: false,
        timestamp: Date.now(),
      };
      return { ...state, liveTurn: live };
    }

    case "token.delta": {
      if (!state.liveTurn || state.liveTurn.turnId !== event.turnId) return state;
      return {
        ...state,
        liveTurn: { ...state.liveTurn, text: state.liveTurn.text + event.text },
      };
    }

    case "turn.committed": {
      const committed: Turn = state.liveTurn
        ? { ...state.liveTurn, text: event.text, committed: true }
        : {
            turnId: event.turnId,
            role: "assistant",
            text: event.text,
            committed: true,
            timestamp: Date.now(),
          };
      const turns = [...state.turns, committed].slice(-MAX_TURNS);
      return { ...state, turns, liveTurn: null };
    }

    default:
      return state;
  }
}
