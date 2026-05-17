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
  /** Number of memories written during this turn. */
  memoryCount?: number;
  /** True when the user cancelled this turn mid-generation. */
  cancelled?: boolean;
}

export interface TurnsState {
  /** Ring buffer: last 200 turns kept in memory; older flushed to SQLite. */
  turns: Turn[];
  /** Live streaming turn (not yet in `turns`). */
  liveTurn: Turn | null;
  /** Memory writes during the current active turn (reset on turn.committed). */
  liveMemoryCount: number;
}

export const initialTurnsState: TurnsState = {
  turns: [],
  liveTurn: null,
  liveMemoryCount: 0,
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
      return { ...state, turns, liveMemoryCount: 0 };
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
      return { ...state, liveTurn: live, liveMemoryCount: 0 };
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
        ? { ...state.liveTurn, text: event.text, committed: true, memoryCount: state.liveMemoryCount || undefined }
        : {
            turnId: event.turnId,
            role: "assistant",
            text: event.text,
            committed: true,
            timestamp: Date.now(),
            memoryCount: state.liveMemoryCount || undefined,
          };
      const turns = [...state.turns, committed].slice(-MAX_TURNS);
      return { ...state, turns, liveTurn: null, liveMemoryCount: 0 };
    }

    case "turn.cancelled": {
      // Commit whatever partial text was streamed so the user can still read it.
      // If no live turn exists (cancelled before turn.started), silently no-op.
      if (!state.liveTurn) return state;
      const cancelled: Turn = { ...state.liveTurn, committed: true, cancelled: true };
      const turns = [...state.turns, cancelled].slice(-MAX_TURNS);
      return { ...state, turns, liveTurn: null, liveMemoryCount: 0 };
    }

    case "memory.written": {
      return { ...state, liveMemoryCount: state.liveMemoryCount + 1 };
    }

    default:
      return state;
  }
}
