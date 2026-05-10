import type { UiState } from "../store.js";
import type { UiEvent } from "../../events/UiEvent.js";

export interface ParliamentOwl {
  owlId: string;
  owlName: string;
  owlEmoji: string;
}

export interface ParliamentDebate {
  debateId: string;
  round: number;
  totalRounds: number;
  owls: ParliamentOwl[];
  positions: Record<string, string>;
  challenges: Record<string, string>;
  synthesis?: string;
  synthOwlId?: string;
  synthOwlName?: string;
  active: boolean;
}

export interface ParliamentState {
  activeDebate: ParliamentDebate | null;
}

export const initialParliamentState: ParliamentState = {
  activeDebate: null,
};

export function applyParliamentEvent(state: UiState, event: UiEvent): UiState {
  switch (event.kind) {
    case "parliament.round.started": {
      const debate: ParliamentDebate = state.activeDebate?.debateId === event.debateId
        ? { ...state.activeDebate, round: event.round, active: true }
        : {
            debateId: event.debateId,
            round: event.round,
            totalRounds: event.totalRounds,
            owls: event.owls,
            positions: {},
            challenges: {},
            active: true,
          };
      return { ...state, activeDebate: debate };
    }

    case "parliament.position.ready": {
      if (!state.activeDebate) return state;
      return {
        ...state,
        activeDebate: {
          ...state.activeDebate,
          positions: {
            ...state.activeDebate.positions,
            [event.owlId]: event.position,
          },
        },
      };
    }

    case "parliament.challenge.ready": {
      if (!state.activeDebate) return state;
      return {
        ...state,
        activeDebate: {
          ...state.activeDebate,
          challenges: {
            ...state.activeDebate.challenges,
            [event.owlId]: event.challenge,
          },
        },
      };
    }

    case "parliament.synthesis.ready": {
      if (!state.activeDebate) return state;
      return {
        ...state,
        activeDebate: {
          ...state.activeDebate,
          synthesis: event.synthesis,
          synthOwlId: event.owlId,
          synthOwlName: event.owlName,
          active: false,
        },
      };
    }

    default:
      return state;
  }
}
